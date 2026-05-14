"""Wind-aware isochrone endpoints.

Wraps :func:`hyplan.planning.compute_isochrone`,
:func:`hyplan.planning.compute_concentric_isochrones`, and
:func:`hyplan.planning.compute_refuel_isochrone`.

Each endpoint returns a GeoJSON FeatureCollection with:
- one ``Polygon`` Feature per budget contour (boundary closed via
  :func:`hyplan.planning.isochrone_polygon`)
- one ``Point`` Feature per ray, carrying the per-ray columns from the
  underlying GeoDataFrame so the frontend can render diagnostics.

``start_time`` is parsed as ISO 8601 (``Z`` suffix accepted).  Wind:

- ``{"kind": "still_air"}`` — default
- ``{"kind": "constant", "speed_kt": ..., "direction_deg": ...}``

Gridded sources (gfs/gmao/merra2) are accepted in the schema for shape
parity with /compute-plan but are rejected at runtime — gridded
isochrones need a planning bbox we don't have yet, and shipping a buggy
version is worse than 400ing with a clear message.
"""

from __future__ import annotations

import datetime
import math

from fastapi import APIRouter, HTTPException

from hyplan.airports import Airport
from hyplan.planning.isochrone import (
    compute_concentric_isochrones,
    compute_isochrone,
    compute_refuel_isochrone,
    isochrone_polygon,
)
from hyplan.units import ureg
from hyplan.waypoint import Waypoint
from hyplan.winds import ConstantWindField, StillAirField

from ..errors import raise_http
from ..schemas import (
    ConcentricIsochroneRequest,
    IsochroneRequest,
    IsochroneStart,
    RefuelIsochroneRequest,
)
from ..state import make_aircraft

router = APIRouter()


# --- helpers -----------------------------------------------------------------

def _resolve_start(spec: IsochroneStart):
    """Resolve an :class:`IsochroneStart` to an :class:`Airport` or
    :class:`Waypoint`.  Airport wins when ICAO is provided."""
    if spec.airport:
        try:
            return Airport(spec.airport)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown airport '{spec.airport}': {exc}",
            )
    if spec.latitude is None or spec.longitude is None:
        raise HTTPException(
            status_code=400,
            detail="start requires either 'airport' or latitude+longitude.",
        )
    if spec.altitude_msl_m is None:
        raise HTTPException(
            status_code=400,
            detail="Waypoint start requires altitude_msl_m.",
        )
    return Waypoint(
        latitude=float(spec.latitude),
        longitude=float(spec.longitude),
        heading=0.0,
        altitude_msl=float(spec.altitude_msl_m) * ureg.meter,
    )


def _build_wind_source(wind: dict):
    kind = (wind or {}).get("kind", "still_air")
    if kind == "still_air":
        return StillAirField()
    if kind == "constant":
        speed = float(wind.get("speed_kt", 0.0))
        direction = float(wind.get("direction_deg", 0.0))
        return ConstantWindField(
            wind_speed=speed * ureg.knot,
            wind_from_deg=direction,
        )
    if kind in ("gfs", "gmao", "merra2"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Wind source '{kind}' is not yet supported for "
                "isochrones; use 'still_air' or 'constant'."
            ),
        )
    raise HTTPException(status_code=400, detail=f"Unknown wind kind: '{kind}'")


def _parse_start_time(s: str | None) -> datetime.datetime | None:
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid start_time '{s}'; expected ISO 8601.",
        )


def _common_kwargs(req: IsochroneRequest, *, start, return_dest) -> dict:
    """Build the kwargs shared by all three compute_* calls."""
    return {
        "aircraft": make_aircraft(req.aircraft),
        "start": start,
        "mode": req.mode,
        "return_destination": return_dest,
        "cruise_altitude": (
            req.cruise_altitude_m * ureg.meter
            if req.cruise_altitude_m is not None else None
        ),
        "on_station_time": req.on_station_time_min * ureg.minute,
        "reserve": req.reserve_min * ureg.minute,
        "start_time": _parse_start_time(req.start_time),
        "wind_source": _build_wind_source(req.wind),
        "azimuth_resolution_deg": req.azimuth_resolution_deg,
        "distance_tolerance_nmi": req.distance_tolerance_nmi,
        "ray_strategy": req.ray_strategy,
    }


def _safe(v):
    """JSON-safe float — NaN/inf become None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _ray_features(gdf, *, extra_cols: tuple[str, ...] = ()) -> list[dict]:
    """One Point Feature per ray with the canonical isochrone columns."""
    base_cols = (
        "azimuth_deg", "target_lat", "target_lon", "distance_nmi",
        "total_time_min", "limiting_leg",
    )
    cols = tuple(c for c in base_cols + extra_cols if c in gdf.columns)
    feats = []
    for _, row in gdf.iterrows():
        props = {c: _safe(row[c]) if c not in ("limiting_leg",) else (
            row[c] if row.get(c) is not None else None
        ) for c in cols}
        # `budget_min` is present in concentric output
        if "budget_min" in gdf.columns:
            props["budget_min"] = _safe(row["budget_min"])
        feats.append({
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [_safe(row["target_lon"]), _safe(row["target_lat"])],
            },
            "properties": props,
        })
    return feats


def _polygon_feature(gdf, *, properties: dict) -> dict | None:
    """Closed boundary polygon, or None if too few solved rays.

    ``isochrone_polygon`` builds the polygon in azimuth order from the
    rays' Point geometries — there's no filtering by reachability, so
    unflyable rays still contribute their (often degenerate) target.
    Callers display this as-is; it's the same shape ``plot_isochrone``
    uses upstream."""
    try:
        poly = isochrone_polygon(gdf)
    except Exception:
        return None
    if poly is None or poly.is_empty:
        return None
    from shapely.geometry import mapping
    return {
        "type": "Feature",
        "geometry": mapping(poly),
        "properties": properties,
    }


def _attrs_to_dict(gdf) -> dict:
    """Coerce gdf.attrs into a JSON-safe summary."""
    out: dict = {}
    for k, v in gdf.attrs.items():
        if isinstance(v, (int, str, bool)) or v is None:
            out[k] = v
        elif isinstance(v, float):
            out[k] = _safe(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [_safe(x) if isinstance(x, float) else x for x in v]
        else:
            out[k] = str(v)
    return out


# --- endpoints ---------------------------------------------------------------

@router.post("/isochrone")
def isochrone(req: IsochroneRequest):
    """Single-budget wind-aware isochrone."""
    start = _resolve_start(req.start)
    return_dest = _resolve_start(req.return_destination) if req.return_destination else None

    try:
        gdf = compute_isochrone(
            budget=req.budget_min * ureg.minute,
            **_common_kwargs(req, start=start, return_dest=return_dest),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("isochrone", exc)

    poly = _polygon_feature(
        gdf,
        properties={
            "budget_min": req.budget_min,
            "mode": req.mode,
            "kind": "isochrone",
        },
    )
    features = [poly] if poly else []
    features.extend(_ray_features(gdf))
    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": _attrs_to_dict(gdf),
    }


@router.post("/isochrone-concentric")
def isochrone_concentric(req: ConcentricIsochroneRequest):
    """Multiple contours in one sweep (e.g. 1h / 2h / 3h)."""
    if not req.budgets_min:
        raise HTTPException(status_code=400, detail="budgets_min must be non-empty.")

    start = _resolve_start(req.start)
    return_dest = _resolve_start(req.return_destination) if req.return_destination else None

    try:
        gdf = compute_concentric_isochrones(
            budgets=[b * ureg.minute for b in req.budgets_min],
            **_common_kwargs(req, start=start, return_dest=return_dest),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("isochrone-concentric", exc)

    features: list[dict] = []
    # One polygon per budget contour.
    for budget_min in sorted(set(req.budgets_min)):
        contour = gdf[gdf["budget_min"].round(3) == round(budget_min, 3)]
        if len(contour) < 3:
            continue
        poly = _polygon_feature(
            contour,
            properties={
                "budget_min": budget_min,
                "mode": req.mode,
                "kind": "isochrone",
            },
        )
        if poly:
            features.append(poly)

    features.extend(_ray_features(gdf))
    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": _attrs_to_dict(gdf),
    }


@router.post("/isochrone-refuel")
def isochrone_refuel(req: RefuelIsochroneRequest):
    """Refuel-aware isochrone (single optional stop)."""
    start = _resolve_start(req.start)
    return_dest = _resolve_start(req.return_destination) if req.return_destination else None

    refuel_airports = []
    for code in req.refuel_airports:
        try:
            refuel_airports.append(Airport(code))
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown refuel airport '{code}': {exc}",
            )

    common = _common_kwargs(req, start=start, return_dest=return_dest)
    # compute_refuel_isochrone takes `sortie_budget` positionally and has
    # no `ray_strategy` argument — pop it out.
    common.pop("ray_strategy", None)

    try:
        gdf = compute_refuel_isochrone(
            sortie_budget=req.budget_min * ureg.minute,
            flight_day_budget=req.flight_day_budget_min * ureg.minute,
            refuel_airports=refuel_airports,
            refuel_time=req.refuel_time_min * ureg.minute,
            max_refuel_stops=req.max_refuel_stops,
            **common,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("isochrone-refuel", exc)

    poly = _polygon_feature(
        gdf,
        properties={
            "budget_min": req.budget_min,
            "flight_day_budget_min": req.flight_day_budget_min,
            "mode": req.mode,
            "kind": "isochrone_refuel",
        },
    )
    features = [poly] if poly else []
    features.extend(_ray_features(
        gdf,
        extra_cols=(
            "itinerary", "refuel_airport", "refuel_count",
            "day_total_time_min", "sortie_cycle_1_min",
            "sortie_cycle_2_min", "sortie_margin_min", "day_margin_min",
        ),
    ))
    return {
        "type": "FeatureCollection",
        "features": features,
        "summary": _attrs_to_dict(gdf),
    }
