"""Map-analysis overlays: swaths, glint, optimize-azimuth, solar position."""

from __future__ import annotations

import datetime
import logging
import traceback

from fastapi import APIRouter, HTTPException

from hyplan.instruments import create_sensor
from hyplan.units import ureg

from ..errors import raise_http
from ..schemas import (
    GlintRequest,
    OptimizeAzimuthRequest,
    SolarPositionRequest,
    SwathRequest,
)
from ..state import get_campaign

logger = logging.getLogger("hyplan-service")

router = APIRouter()


@router.post("/generate-swaths")
def generate_swaths(req: SwathRequest):
    """Generate swath footprint polygons for selected flight lines."""
    from hyplan.swath import generate_swath_polygon, analyze_swath_gaps_overlaps
    from shapely.geometry import mapping as shapely_mapping

    campaign = get_campaign(req.campaign_id)
    lines_by_id = campaign.all_flight_lines_dict()

    try:
        sensor = create_sensor(req.sensor)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sensor: {exc}")

    swath_polygons = []
    features = []
    warnings = []

    for lid in req.line_ids:
        if lid not in lines_by_id:
            warnings.append(f"Unknown line_id: '{lid}'")
            continue
        line = lines_by_id[lid]
        try:
            poly = generate_swath_polygon(line, sensor)
            swath_polygons.append(poly)
            features.append({
                "type": "Feature",
                "geometry": shapely_mapping(poly),
                "properties": {
                    "line_id": lid,
                    "site_name": line.site_name or lid,
                },
            })
        except Exception as exc:
            warnings.append(f"Swath failed for {lid}: {exc}")

    gap_overlap_info = {}
    if len(swath_polygons) >= 2:
        try:
            df = analyze_swath_gaps_overlaps(swath_polygons)
            gap_overlap_info = {
                "total_pairs": len(df),
                "overlapping_pairs": int((df["overlap_area_m2"] > 0).sum()) if "overlap_area_m2" in df.columns else 0,
                "gap_pairs": int((df["gap_distance_m"] > 0).sum()) if "gap_distance_m" in df.columns else 0,
            }
        except Exception:
            pass

    return {
        "swaths": {
            "type": "FeatureCollection",
            "features": features,
        },
        "count": len(features),
        "gap_overlap": gap_overlap_info,
        "warnings": warnings,
    }


@router.post("/compute-glint")
def compute_glint(req: GlintRequest):
    """Compute per-swath-sample glint angles for selected flight lines.

    Reproduces the visualization from notebooks/glint_analysis.ipynb
    Cell 10: one Point feature per cross-track sample colored by
    ``glint_angle``.
    """
    from hyplan.glint import compute_glint_vectorized, fraction_exceeding_glint_threshold
    from hyplan.sun import sunpos

    campaign = get_campaign(req.campaign_id)
    lines_by_id = campaign.all_flight_lines_dict()

    try:
        sensor = create_sensor(req.sensor)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sensor: {exc}")

    try:
        obs_time = datetime.datetime.fromisoformat(req.takeoff_time.replace('Z', '+00:00'))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid takeoff_time: '{req.takeoff_time}'")

    features: list = []
    summary: list = []
    warnings: list = []
    any_sun_below_horizon = False

    for lid in req.line_ids:
        if lid not in lines_by_id:
            warnings.append(f"Unknown line_id: '{lid}'")
            continue
        line = lines_by_id[lid]
        try:
            gdf = compute_glint_vectorized(line, sensor, obs_time)
        except Exception as exc:
            # Per-line failures don't fail the whole request — the frontend
            # surfaces them in the warnings list.
            logger.error("compute-glint failed for %s: %s", lid, traceback.format_exc())
            warnings.append(f"Glint failed for {lid}: {exc}")
            continue

        mid_lat = (line.lat1 + line.lat2) / 2.0
        mid_lon = (line.lon1 + line.lon2) / 2.0
        alt_m = line.altitude_msl.magnitude if line.altitude_msl else 0.0
        try:
            sol_az, sol_zen, *_ = sunpos(
                dt=obs_time, latitude=mid_lat, longitude=mid_lon,
                elevation=alt_m, radians=False,
            )
            sol_az_f = float(sol_az) if hasattr(sol_az, "__len__") is False else float(sol_az.item() if hasattr(sol_az, "item") else sol_az)
            sol_zen_f = float(sol_zen) if hasattr(sol_zen, "__len__") is False else float(sol_zen.item() if hasattr(sol_zen, "item") else sol_zen)
        except Exception:
            sol_az_f = None
            sol_zen_f = None
        if sol_zen_f is not None and sol_zen_f > 90.0:
            any_sun_below_horizon = True

        n = len(gdf)
        if n > req.max_points_per_line and n > 0:
            stride = (n + req.max_points_per_line - 1) // req.max_points_per_line
            gdf_render = gdf.iloc[::stride]
        else:
            gdf_render = gdf

        for row in gdf_render.itertuples(index=False):
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [float(row.target_longitude), float(row.target_latitude)],
                },
                "properties": {
                    "line_id": lid,
                    "glint_angle": float(row.glint_angle),
                    "tilt_angle": float(row.tilt_angle),
                    "along_track_distance": float(row.along_track_distance),
                },
            })

        try:
            frac = fraction_exceeding_glint_threshold(gdf, req.threshold_deg)
        except Exception:
            frac = None

        summary.append({
            "line_id": lid,
            "site_name": line.site_name or lid,
            "n_samples": int(n),
            "mean_glint": float(gdf["glint_angle"].mean()) if n else None,
            "min_glint": float(gdf["glint_angle"].min()) if n else None,
            "max_glint": float(gdf["glint_angle"].max()) if n else None,
            "fraction_below_threshold": float(frac) if frac is not None else None,
            "solar_azimuth": sol_az_f,
            "solar_zenith": sol_zen_f,
        })

    if any_sun_below_horizon:
        warnings.insert(
            0,
            "Sun is below the horizon (solar_zenith > 90°) at the observation time. "
            "Glint angles are not physically meaningful for nighttime. "
            "Check that takeoff_time matches the intended UTC observation time."
        )

    return {
        "glint": {
            "type": "FeatureCollection",
            "features": features,
        },
        "summary": summary,
        "threshold_deg": req.threshold_deg,
        "takeoff_time": req.takeoff_time,
        "sensor": req.sensor,
        "warnings": warnings,
        "sun_below_horizon": any_sun_below_horizon,
    }


@router.post("/optimize-azimuth")
def optimize_azimuth(req: OptimizeAzimuthRequest):
    """Sweep flight-line azimuth at a test point and return the heading
    that maximizes glint angle, mirroring
    ``notebooks/glint_analysis.ipynb`` Cell 12.

    Returns the full sweep for plotting plus the picked optimum.
    """
    import numpy as np
    from hyplan.flight_line import FlightLine
    from hyplan.glint import compute_glint_vectorized
    from hyplan.sun import sunpos

    try:
        sensor = create_sensor(req.sensor)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sensor: {exc}")

    try:
        obs_time = datetime.datetime.fromisoformat(req.takeoff_time.replace('Z', '+00:00'))
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid takeoff_time: '{req.takeoff_time}'")

    if req.criterion not in ("max_mean", "max_min"):
        raise HTTPException(status_code=400, detail="criterion must be 'max_mean' or 'max_min'")
    if req.step_deg <= 0 or req.step_deg > 90:
        raise HTTPException(status_code=400, detail="step_deg must be in (0, 90]")
    if req.leg_length_m <= 0:
        raise HTTPException(status_code=400, detail="leg_length_m must be positive")

    try:
        sol_az, sol_zen, *_ = sunpos(
            dt=obs_time, latitude=req.lat, longitude=req.lon,
            elevation=req.altitude_msl_m, radians=False,
        )
        sol_az_f = float(sol_az.item()) if hasattr(sol_az, "item") else float(sol_az)
        sol_zen_f = float(sol_zen.item()) if hasattr(sol_zen, "item") else float(sol_zen)
    except Exception:
        sol_az_f = None
        sol_zen_f = None
    sun_below_horizon = bool(sol_zen_f is not None and sol_zen_f > 90.0)

    headings = list(np.arange(0.0, 360.0, req.step_deg))
    mean_glints: list = []
    min_glints: list = []
    max_glints: list = []

    for hdg in headings:
        fl = FlightLine.center_length_azimuth(
            lat=req.lat, lon=req.lon,
            length=req.leg_length_m * ureg.meter,
            az=float(hdg),
            altitude_msl=req.altitude_msl_m * ureg.meter,
            site_name=f"sweep-{int(hdg):03d}",
        )
        try:
            gdf = compute_glint_vectorized(fl, sensor, obs_time)
            mean_glints.append(float(gdf["glint_angle"].mean()))
            min_glints.append(float(gdf["glint_angle"].min()))
            max_glints.append(float(gdf["glint_angle"].max()))
        except Exception:
            mean_glints.append(float("nan"))
            min_glints.append(float("nan"))
            max_glints.append(float("nan"))

    arr = np.array(mean_glints if req.criterion == "max_mean" else min_glints)
    valid = ~np.isnan(arr)
    if not valid.any():
        raise HTTPException(
            status_code=400,
            detail={
                "message": "All azimuth sweep samples failed — solar geometry is unfavourable at every heading for this site and time.",
                "code": "no_valid_sweep_samples",
                "operation": "optimize-azimuth",
            },
        )
    idx_best = int(np.nanargmax(arr))
    optimal_azimuth = float(headings[idx_best])
    optimal_value = float(arr[idx_best])

    warnings: list = []
    if sun_below_horizon:
        warnings.append(
            "Sun is below the horizon (solar_zenith > 90°) at the test point — "
            "the 'optimum' heading is not physically meaningful for nighttime."
        )

    return {
        "lat": req.lat,
        "lon": req.lon,
        "altitude_msl_m": req.altitude_msl_m,
        "sensor": req.sensor,
        "takeoff_time": req.takeoff_time,
        "leg_length_m": req.leg_length_m,
        "step_deg": req.step_deg,
        "criterion": req.criterion,
        "headings": [float(h) for h in headings],
        "mean_glint": mean_glints,
        "min_glint": min_glints,
        "max_glint": max_glints,
        "optimal_azimuth": optimal_azimuth,
        "optimal_value": optimal_value,
        "solar_azimuth": sol_az_f,
        "solar_zenith": sol_zen_f,
        "sun_below_horizon": sun_below_horizon,
        "warnings": warnings,
    }


@router.post("/solar-position")
def solar_position(req: SolarPositionRequest):
    """Return a 24-hour time series of solar position at ``(lat, lon)``.

    Used by the plugin's "Solar Position" panel to plot solar zenith vs
    UTC time at a user-chosen point.  The full curve (including night)
    is returned so the chart can show sub-horizon values.
    """
    from hyplan.sun import solar_position_increments

    try:
        df = solar_position_increments(
            latitude=req.lat,
            longitude=req.lon,
            date=req.date,
            min_elevation=-90.0,           # full 24h, including night
            timezone_offset=0,             # keep times in UTC
            increment=f"{req.increment_min}min",
        )
    except Exception as exc:
        raise_http("solar-position", exc)

    times = df["Time"].tolist()
    elevation = [float(e) for e in df["Elevation"].tolist()]
    azimuth = [float(a) for a in df["Azimuth"].tolist()]
    zenith = [90.0 - e for e in elevation]

    def _interp_cross(idx0: int, idx1: int) -> str:
        """Linear-interp the elevation=0 crossing between two adjacent samples."""
        e0, e1 = elevation[idx0], elevation[idx1]
        if e1 == e0:
            return times[idx0]
        frac = (0.0 - e0) / (e1 - e0)
        h0, m0, s0 = (int(x) for x in times[idx0].split(":"))
        h1, m1, s1 = (int(x) for x in times[idx1].split(":"))
        t0 = h0 * 60 + m0 + s0 / 60.0
        t1 = h1 * 60 + m1 + s1 / 60.0
        # Wrap-around at the end of the day (last sample is e.g. 23:50)
        if t1 < t0:
            t1 += 24 * 60
        t = t0 + frac * (t1 - t0)
        h = int(t // 60) % 24
        m = int(t % 60)
        return f"{h:02d}:{m:02d}"

    sunrise_utc = None
    sunset_utc = None
    for i in range(1, len(elevation)):
        if elevation[i - 1] < 0 <= elevation[i] and sunrise_utc is None:
            sunrise_utc = _interp_cross(i - 1, i)
        if elevation[i - 1] >= 0 > elevation[i]:
            sunset_utc = _interp_cross(i - 1, i)

    return {
        "lat": req.lat,
        "lon": req.lon,
        "date": req.date,
        "increment_min": req.increment_min,
        "time_utc": times,
        "elevation_deg": elevation,
        "zenith_deg": zenith,
        "azimuth_deg": azimuth,
        "sunrise_utc": sunrise_utc,
        "sunset_utc": sunset_utc,
    }
