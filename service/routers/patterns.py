"""Pattern generation, regeneration, transformation, listing, deletion.

A HyPlan :class:`Pattern` is a first-class object on the campaign that
owns a set of flight lines and/or waypoints.  Patterns are referenced
from compute sequences, regenerated in place with parameter overrides,
moved as a whole via translate / move_to / rotate, and have
specialized rendering on the frontend (e.g. the colored swath preview
for ``glint_arc``).
"""

from __future__ import annotations

import datetime
import logging
import traceback
from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from hyplan.instruments import create_sensor
from hyplan.units import ureg

from ..errors import raise_http
from ..schemas import (
    DeletePatternRequest,
    PatternRequest,
    ReplacePatternRequest,
    TransformPatternRequest,
)
from ..state import (
    check_revision,
    get_campaign,
    get_or_create_campaign,
    make_aircraft,
    persist_campaign,
)

logger = logging.getLogger("hyplan-service")

router = APIRouter()


def _invoke_pattern_generator(
    kind: str,
    center: tuple,
    heading: float,
    altitude_msl_m: float,
    params: dict,
    takeoff_time: Optional[str] = None,
    aircraft: Optional[str] = None,
):
    """Dispatch to the right hyplan generator; always returns a ``Pattern``.

    ``takeoff_time`` and ``aircraft`` are required only for
    ``glint_arc``; ignored for other kinds.
    """
    from hyplan.flight_patterns import (
        racetrack, rosette, polygon as poly_pattern, sawtooth, spiral, glint_arc,
    )

    if kind == "racetrack":
        return racetrack(
            center=center, heading=heading,
            altitude=altitude_msl_m * ureg.meter,
            leg_length=params.get("leg_length_m", 10000) * ureg.meter,
            n_legs=params.get("n_legs", 1),
            offset=(params.get("offset_m", 0) * ureg.meter
                    if params.get("offset_m") else 0 * ureg.meter),
        )
    if kind == "rosette":
        return rosette(
            center=center, heading=heading,
            altitude=altitude_msl_m * ureg.meter,
            radius=params.get("radius_m", 5000) * ureg.meter,
            n_lines=params.get("n_lines", 3),
        )
    if kind == "polygon":
        return poly_pattern(
            center=center, heading=heading,
            altitude=altitude_msl_m * ureg.meter,
            radius=params.get("radius_m", 5000) * ureg.meter,
            n_sides=int(params.get("n_sides", 4)),
            aspect_ratio=float(params.get("aspect_ratio", 1.0)),
        )
    if kind == "sawtooth":
        return sawtooth(
            center=center, heading=heading,
            altitude_min=params.get("altitude_min_m", 1000) * ureg.meter,
            altitude_max=params.get("altitude_max_m", 3000) * ureg.meter,
            leg_length=params.get("leg_length_m", 10000) * ureg.meter,
            n_cycles=params.get("n_cycles", 2),
        )
    if kind == "spiral":
        return spiral(
            center=center, heading=heading,
            altitude_start=params.get("altitude_start_m", 500) * ureg.meter,
            altitude_end=params.get("altitude_end_m", 3000) * ureg.meter,
            radius=params.get("radius_m", 3000) * ureg.meter,
            n_turns=float(params.get("n_turns", 3)),
            direction=str(params.get("direction", "right")),
        )
    if kind == "glint_arc":
        if not takeoff_time:
            raise HTTPException(status_code=400, detail="glint_arc requires takeoff_time (UTC).")
        if not aircraft:
            raise HTTPException(status_code=400, detail="glint_arc requires aircraft (for cruise speed).")
        try:
            obs_dt = datetime.datetime.fromisoformat(takeoff_time.replace("Z", "+00:00"))
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid takeoff_time: '{takeoff_time}'")
        ac = make_aircraft(aircraft)
        alt_q = altitude_msl_m * ureg.meter
        speed_q = ac.cruise_speed_at(alt_q)
        cl_m = params.get("collection_length_m")
        cl_q = cl_m * ureg.meter if cl_m not in (None, "") else None
        bank = params.get("bank_angle")
        if bank in ("", None):
            bank = None
        else:
            bank = float(bank)
        return glint_arc(
            center=center,
            observation_datetime=obs_dt,
            altitude=alt_q,
            speed=speed_q,
            bank_angle=bank,
            bank_direction=str(params.get("bank_direction", "right")),
            collection_length=cl_q,
        )
    raise HTTPException(status_code=400, detail=f"Unknown pattern: '{kind}'")


def _pattern_response_payload(campaign, pattern) -> dict:
    """Canonical response body for pattern mutations."""
    return {
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
        "pattern_id": pattern.pattern_id,
        "pattern_kind": pattern.kind,
        "pattern_name": pattern.name,
        "pattern_params": pattern.params,
        "is_line_based": pattern.is_line_based,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
    }


def _compute_arc_glint_preview(pattern, sensor_name: str, max_points: int = 4000) -> dict:
    """Build ``{arc_swath, arc_glint, arc_glint_summary}`` for a glint_arc.

    Reconstructs the underlying :class:`GlintArc` from
    ``pattern.params``, computes the swath footprint polygon and
    per-sample glint angles, and returns GeoJSON-ready dicts for direct
    rendering by the plugin frontend.
    """
    from hyplan.glint import GlintArc, compute_glint_arc, fraction_exceeding_glint_threshold
    from shapely.geometry import mapping as shapely_mapping

    sensor = create_sensor(sensor_name)
    p = pattern.params
    obs_dt = datetime.datetime.fromisoformat(
        p["observation_datetime"].replace("Z", "+00:00")
    )
    cl_m = p.get("collection_length_m")
    arc = GlintArc(
        target_lat=float(p["center_lat"]),
        target_lon=float(p["center_lon"]),
        observation_datetime=obs_dt,
        altitude_msl=float(p["altitude_msl_m"]) * ureg.meter,
        speed=float(p["speed_mps"]) * (ureg.meter / ureg.second),
        bank_angle=p.get("bank_angle"),
        bank_direction=str(p.get("bank_direction", "right")),
        collection_length=(float(cl_m) * ureg.meter if cl_m is not None else None),
    )

    footprint = arc.footprint(sensor)
    arc_swath = {
        "type": "Feature",
        "geometry": shapely_mapping(footprint),
        "properties": {
            "pattern_id": pattern.pattern_id,
            "pattern_kind": pattern.kind,
            "name": pattern.name,
        },
    }

    gdf = compute_glint_arc(arc, sensor)
    n = len(gdf)
    if n > max_points and n > 0:
        stride = (n + max_points - 1) // max_points
        gdf_render = gdf.iloc[::stride]
    else:
        gdf_render = gdf

    features = []
    for row in gdf_render.itertuples(index=False):
        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [float(row.target_longitude), float(row.target_latitude)],
            },
            "properties": {
                "pattern_id": pattern.pattern_id,
                "glint_angle": float(row.glint_angle),
                "tilt_angle": float(row.tilt_angle),
                "along_track_distance": float(row.along_track_distance),
            },
        })
    arc_glint = {"type": "FeatureCollection", "features": features}

    threshold = 25.0
    try:
        frac = float(fraction_exceeding_glint_threshold(gdf, threshold))
    except Exception:
        frac = None

    summary = {
        "pattern_id": pattern.pattern_id,
        "pattern_name": pattern.name,
        "n_samples": int(n),
        "mean_glint": float(gdf["glint_angle"].mean()) if n else None,
        "min_glint": float(gdf["glint_angle"].min()) if n else None,
        "max_glint": float(gdf["glint_angle"].max()) if n else None,
        "fraction_below_threshold": frac,
        "threshold_deg": threshold,
        "sensor": sensor_name,
    }

    return {
        "arc_swath": arc_swath,
        "arc_glint": arc_glint,
        "arc_glint_summary": summary,
    }


@router.post("/generate-pattern")
def generate_pattern(
    req: PatternRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Generate a flight pattern and add it to the campaign."""
    campaign = get_or_create_campaign(
        req.campaign_id, req.campaign_name, req.campaign_bounds,
    )
    check_revision(campaign, if_match)

    try:
        pattern = _invoke_pattern_generator(
            req.pattern,
            (req.center_lat, req.center_lon),
            req.heading,
            req.altitude_msl_m,
            req.params,
            takeoff_time=req.takeoff_time,
            aircraft=req.aircraft,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # HyPlanValueError (e.g. solar zenith out of range for glint_arc)
        # and HyPlanTypeError are user-actionable — classify maps them
        # to 400.  Anything else becomes a 500.
        raise_http("generate-pattern", exc)

    existing_of_kind = sum(1 for p in campaign.patterns if p.kind == req.pattern)
    pattern.name = f"{req.pattern.capitalize()} {existing_of_kind + 1}"

    campaign.add_pattern(pattern)
    persist_campaign(campaign)
    payload = _pattern_response_payload(campaign, pattern)

    # For glint_arc, also compute the swath footprint and per-sample
    # glint so the UI can render the colored arc swath in one
    # round-trip.  Failure is non-fatal — the arc itself is in the
    # response.
    if pattern.kind == "glint_arc" and req.sensor:
        try:
            payload.update(_compute_arc_glint_preview(pattern, req.sensor))
        except Exception:
            logger.warning("glint_arc preview failed: %s", traceback.format_exc())

    return payload


@router.post("/delete-pattern")
def delete_pattern(
    req: DeletePatternRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Delete a pattern and all its legs/waypoints from the campaign."""
    campaign = get_campaign(req.campaign_id)
    check_revision(campaign, if_match)
    try:
        campaign.remove_pattern(req.pattern_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    persist_campaign(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
    }


@router.post("/replace-pattern")
def replace_pattern(
    req: ReplacePatternRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Regenerate a pattern in place with parameter overrides.

    ``overrides`` is merged into the pattern's stored params
    (meters/degrees) before re-invoking the generator.  The
    ``pattern_id`` is preserved; contained flight lines receive fresh
    ``line_id``\\ s.
    """
    campaign = get_campaign(req.campaign_id)
    check_revision(campaign, if_match)
    try:
        old = campaign.get_pattern(req.pattern_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        new_pattern = old.regenerate(**req.overrides)
    except Exception as exc:
        raise_http("replace-pattern", exc)

    campaign.replace_pattern(req.pattern_id, new_pattern)
    persist_campaign(campaign)
    return _pattern_response_payload(campaign, new_pattern)


@router.post("/transform-pattern")
def transform_pattern(
    req: TransformPatternRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Move a whole pattern in place via HyPlan's ``Pattern`` movement DSL.

    Three operations, each preserving the pattern_id:

    - ``translate`` — geodesic offset by ``north_m``, ``east_m``
    - ``move_to``   — re-anchor at ``latitude`` / ``longitude``
    - ``rotate``    — rotate by ``angle_deg`` about an optional
                       ``(around_lat, around_lon)`` pivot (defaults to
                       the pattern's center)

    Contained flight lines receive fresh ``line_id``\\ s.  The
    ``pattern_id`` is preserved so any compute sequences referencing it
    keep working.
    """
    campaign = get_campaign(req.campaign_id)
    check_revision(campaign, if_match)
    try:
        old = campaign.get_pattern(req.pattern_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    op = req.operation
    p = req.params
    try:
        if op == "translate":
            north = float(p.get("north_m", 0)) * ureg.meter
            east = float(p.get("east_m", 0)) * ureg.meter
            new_pattern = old.translate(north, east)
        elif op == "move_to":
            if "latitude" not in p or "longitude" not in p:
                raise HTTPException(
                    status_code=400,
                    detail="move_to requires 'latitude' and 'longitude' in params.",
                )
            new_pattern = old.move_to(float(p["latitude"]), float(p["longitude"]))
        elif op == "rotate":
            angle = float(p.get("angle_deg", 0))
            around = None
            if "around_lat" in p and "around_lon" in p:
                around = (float(p["around_lat"]), float(p["around_lon"]))
            new_pattern = old.rotate(angle, around=around)
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown operation: '{op}'.  Expected translate, move_to, or rotate.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("transform-pattern", exc)

    campaign.replace_pattern(req.pattern_id, new_pattern)
    persist_campaign(campaign)
    return _pattern_response_payload(campaign, new_pattern)


@router.get("/patterns/{campaign_id}")
def list_patterns(campaign_id: str):
    """List all patterns attached to a campaign."""
    campaign = get_campaign(campaign_id)
    return {
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
        "patterns": [
            {
                "pattern_id": p.pattern_id,
                "kind": p.kind,
                "name": p.name,
                "is_line_based": p.is_line_based,
                "line_ids": p.line_ids,
                "waypoint_count": len(p.waypoints),
                "params": p.params,
            }
            for p in campaign.patterns
        ],
    }
