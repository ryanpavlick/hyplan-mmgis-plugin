"""Flight plan computation and route optimization."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException

from hyplan.airports import Airport
from hyplan.flight_optimizer import greedy_optimize
from hyplan.planning import compute_flight_plan
from hyplan.units import ureg
from hyplan.waypoint import Waypoint

from ..errors import raise_http
from ..schemas import (
    ComparePlansRequest,
    ComputePlanRequest,
    ComputePlanResponse,
    OptimizeRequest,
    OptimizeResponse,
)
from ..state import get_campaign, make_aircraft, set_plan

router = APIRouter()


@router.post("/compute-plan", response_model=ComputePlanResponse)
def compute_plan(req: ComputePlanRequest):
    """Compute a flight plan from an ordered sequence."""
    warnings: list[str] = []
    # Campaign is optional for waypoint-only sequences
    campaign = None
    if req.campaign_id:
        try:
            campaign = get_campaign(req.campaign_id)
        except HTTPException:
            if any(e.kind in ("line", "pattern") for e in req.sequence):
                raise  # Need campaign for line/pattern references
    aircraft = make_aircraft(req.aircraft)
    flight_sequence = []

    for entry in req.sequence:
        if entry.kind == "line":
            try:
                line = campaign.get_line(entry.line_id) if campaign else None
            except Exception:
                line = None
            if line is None:
                raise HTTPException(status_code=400, detail=f"Unknown line_id: '{entry.line_id}'")
            if entry.reversed:
                line = line.reverse()
            flight_sequence.append(line)
        elif entry.kind == "pattern":
            if not campaign:
                raise HTTPException(status_code=400, detail="Pattern reference requires a campaign.")
            try:
                pattern = campaign.get_pattern(entry.pattern_id)
            except Exception:
                raise HTTPException(status_code=400, detail=f"Unknown pattern_id: '{entry.pattern_id}'")
            flight_sequence.append(pattern)
        elif entry.kind == "waypoint":
            if entry.latitude is None or entry.longitude is None:
                raise HTTPException(status_code=400, detail="Waypoint requires latitude and longitude.")
            wp = Waypoint(
                latitude=entry.latitude,
                longitude=entry.longitude,
                heading=0.0,
                altitude_msl=(entry.altitude_msl_m or 3000.0) * ureg.meter,
            )
            flight_sequence.append(wp)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown sequence kind: '{entry.kind}'")

    # Wind kwargs
    wind_kwargs = {}
    wind_kind = req.wind.get("kind", "still_air")
    if wind_kind == "constant":
        speed = req.wind.get("speed_kt", 0.0)
        direction = req.wind.get("direction_deg", 0.0)
        if speed > 0:
            wind_kwargs["wind_speed"] = speed * ureg.knot
            wind_kwargs["wind_direction"] = float(direction)

    # Airports
    takeoff_airport = Airport(req.takeoff_airport) if req.takeoff_airport else None
    return_airport = Airport(req.return_airport) if req.return_airport else None

    try:
        takeoff_time = None
        if req.takeoff_time:
            try:
                takeoff_time = datetime.datetime.fromisoformat(req.takeoff_time.replace('Z', '+00:00'))
            except ValueError:
                warnings.append(f"Invalid takeoff_time format: '{req.takeoff_time}', ignoring.")

        plan = compute_flight_plan(
            aircraft=aircraft,
            flight_sequence=flight_sequence,
            takeoff_airport=takeoff_airport,
            return_airport=return_airport,
            takeoff_time=takeoff_time,
            **wind_kwargs,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("compute-plan", exc)

    # Convert plan GeoDataFrame to GeoJSON
    plan_geojson = {
        "type": "FeatureCollection",
        "features": [],
    }
    from shapely.geometry import mapping
    for _, row in plan.iterrows():
        geom = row.get("geometry")
        if geom is None or geom.is_empty:
            continue
        feature = {
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "segment_type": row.get("segment_type", ""),
                "segment_name": row.get("segment_name", ""),
                "distance": float(row.get("distance", 0) or 0),
                "time_to_segment": float(row.get("time_to_segment", 0) or 0),
                "start_altitude": float(row.get("start_altitude", 0) or 0),
                "end_altitude": float(row.get("end_altitude", 0) or 0),
            },
        }
        plan_geojson["features"].append(feature)

    summary = {
        "segments": len(plan),
        "total_distance_nm": float(plan["distance"].fillna(0).sum()),
        "total_time_min": float(plan["time_to_segment"].fillna(0).sum()),
        "flight_line_segments": int((plan["segment_type"] == "flight_line").sum()),
    }

    set_plan(req.campaign_id, plan)

    return ComputePlanResponse(
        segments=plan_geojson,
        summary=summary,
        warnings=warnings,
    )


@router.post("/optimize-sequence", response_model=OptimizeResponse)
def optimize_sequence(req: OptimizeRequest):
    """Propose an optimized line ordering."""
    warnings: list[str] = []
    campaign = get_campaign(req.campaign_id)
    aircraft = make_aircraft(req.aircraft)

    lines_by_id = campaign.all_flight_lines_dict()
    flight_lines = []
    for lid in req.line_ids:
        if lid not in lines_by_id:
            raise HTTPException(status_code=400, detail=f"Unknown line_id: '{lid}'")
        flight_lines.append(lines_by_id[lid])

    takeoff = Airport(req.takeoff_airport)
    return_apt = Airport(req.return_airport) if req.return_airport else None

    try:
        result = greedy_optimize(
            aircraft=aircraft,
            flight_lines=flight_lines,
            airports=[takeoff] + ([return_apt] if return_apt else []),
            takeoff_airport=takeoff,
            return_airport=return_apt,
            max_endurance=req.max_endurance,
            max_daily_flight_time=req.max_daily_flight_time,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("optimize-sequence", exc)

    # Map optimized FlightLine objects back to line IDs
    optimized_lines = result["flight_sequence"]
    id_by_obj = {id(fl): lid for lid, fl in lines_by_id.items()}

    proposed = []
    for fl in optimized_lines:
        obj_id = id(fl)
        if obj_id in id_by_obj:
            proposed.append({"kind": "line", "line_id": id_by_obj[obj_id], "reversed": False})
        else:
            # Try matching by endpoints (reversed copy)
            for lid, orig in lines_by_id.items():
                if (abs(fl.lat1 - orig.lat2) < 1e-6 and abs(fl.lon1 - orig.lon2) < 1e-6):
                    proposed.append({"kind": "line", "line_id": lid, "reversed": True})
                    break
            else:
                warnings.append("Could not map optimized line back to campaign ID")

    return OptimizeResponse(
        proposed_sequence=proposed,
        total_time=result.get("total_time", 0.0),
        daily_times=result.get("daily_times", []),
        lines_covered=result.get("lines_covered", 0),
        lines_skipped=result.get("lines_skipped", []),
        warnings=warnings,
    )


def _segment_props(feature: dict) -> dict:
    """Pull the properties dict out of a plan GeoJSON Feature.
    Returns ``{}`` for malformed features so downstream code can
    still index into it safely."""
    if not isinstance(feature, dict):
        return {}
    p = feature.get("properties")
    return p if isinstance(p, dict) else {}


def _delta(a: float | None, b: float | None) -> float | None:
    """``b - a`` with ``None``-tolerance.  Returns ``None`` if either
    side is missing."""
    if a is None or b is None:
        return None
    try:
        return float(b) - float(a)
    except (TypeError, ValueError):
        return None


@router.post("/compare-plans")
def compare_plans(req: ComparePlansRequest):
    """Diff two computed plans segment-by-segment.

    Each input is a GeoJSON FeatureCollection in the shape
    ``/compute-plan`` returns under ``segments``.  Segments are
    paired **by index** — the most useful comparison is "same
    sequence, different wind / aircraft", where segments line up
    1-to-1.  When the plans have different lengths, the extra
    segments at the tail are reported as ``added`` (in B but not A)
    or ``removed`` (in A but not B).

    Per-segment fields in the response:

    - ``status`` — ``matched`` | ``added`` | ``removed``
    - ``segment_type``, ``segment_name`` — copied from whichever
      side has them (B preferred for matched / added)
    - ``delta_distance_nm``, ``delta_time_min``,
      ``delta_start_altitude``, ``delta_end_altitude`` — ``B - A``;
      ``None`` for added/removed

    The summary aggregates the deltas over matched segments.
    """
    features_a = req.plan_a.get("features", []) if isinstance(req.plan_a, dict) else []
    features_b = req.plan_b.get("features", []) if isinstance(req.plan_b, dict) else []
    if not isinstance(features_a, list) or not isinstance(features_b, list):
        raise HTTPException(
            status_code=400,
            detail="plan_a.features and plan_b.features must be lists.",
        )

    n = max(len(features_a), len(features_b))
    diff_segments: list[dict] = []
    total_dd = 0.0
    total_dt = 0.0
    matched = added = removed = 0

    for i in range(n):
        a = features_a[i] if i < len(features_a) else None
        b = features_b[i] if i < len(features_b) else None
        ap = _segment_props(a) if a is not None else {}
        bp = _segment_props(b) if b is not None else {}

        if a is None:
            status = "added"
            added += 1
            row: dict = {
                "index": i,
                "status": status,
                "segment_type": bp.get("segment_type"),
                "segment_name": bp.get("segment_name"),
                "b": bp,
            }
        elif b is None:
            status = "removed"
            removed += 1
            row = {
                "index": i,
                "status": status,
                "segment_type": ap.get("segment_type"),
                "segment_name": ap.get("segment_name"),
                "a": ap,
            }
        else:
            status = "matched"
            matched += 1
            dd = _delta(ap.get("distance"), bp.get("distance"))
            dt = _delta(ap.get("time_to_segment"), bp.get("time_to_segment"))
            d_alt_start = _delta(ap.get("start_altitude"), bp.get("start_altitude"))
            d_alt_end = _delta(ap.get("end_altitude"), bp.get("end_altitude"))
            if dd is not None:
                total_dd += dd
            if dt is not None:
                total_dt += dt
            row = {
                "index": i,
                "status": status,
                "segment_type": bp.get("segment_type") or ap.get("segment_type"),
                "segment_name": bp.get("segment_name") or ap.get("segment_name"),
                "delta_distance_nm": dd,
                "delta_time_min": dt,
                "delta_start_altitude": d_alt_start,
                "delta_end_altitude": d_alt_end,
                "a": ap,
                "b": bp,
            }
        diff_segments.append(row)

    return {
        "label_a": req.label_a,
        "label_b": req.label_b,
        "summary": {
            "matched": matched,
            "added": added,
            "removed": removed,
            "segments_a": len(features_a),
            "segments_b": len(features_b),
            "delta_segments": len(features_b) - len(features_a),
            # Sum-of-deltas over MATCHED segments only.  Adding the
            # full A/B totals here would double-count for an added
            # segment (its B-distance shows up in "added" too).
            "delta_distance_nm": total_dd,
            "delta_time_min": total_dt,
        },
        "segments": diff_segments,
    }
