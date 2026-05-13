"""Wind grid endpoint: U/V slices for leaflet-velocity visualization."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException

from hyplan.units import ureg

from ..errors import raise_http
from ..schemas import WindGridRequest

router = APIRouter()


@router.post("/wind-grid")
def wind_grid(req: WindGridRequest):
    """Return a U/V wind grid for leaflet-velocity visualization."""
    import numpy as np

    try:
        target_time = datetime.datetime.fromisoformat(req.time.replace('Z', '+00:00'))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid time format: '{req.time}'")

    min_lon, min_lat, max_lon, max_lat = req.bounds

    # Convert altitude to approximate pressure level
    from hyplan.atmosphere import pressure_at
    pressure_hpa = pressure_at(req.altitude_m * ureg.meter).m_as(ureg.hectopascal)

    try:
        if req.source == "gfs":
            from hyplan.winds import GFSWindField
            wf = GFSWindField(
                lat_min=min_lat, lat_max=max_lat,
                lon_min=min_lon, lon_max=max_lon,
                time_start=target_time - datetime.timedelta(hours=1),
                time_end=target_time + datetime.timedelta(hours=1),
                pressure_min_hpa=max(pressure_hpa - 50, 50),
                pressure_max_hpa=min(pressure_hpa + 50, 1000),
            )
        elif req.source == "gmao":
            from hyplan.winds import GMAOWindField
            wf = GMAOWindField(
                lat_min=min_lat, lat_max=max_lat,
                lon_min=min_lon, lon_max=max_lon,
                time_start=target_time - datetime.timedelta(hours=2),
                time_end=target_time + datetime.timedelta(hours=2),
                pressure_min_hpa=max(pressure_hpa - 50, 50),
                pressure_max_hpa=min(pressure_hpa + 50, 1000),
            )
        elif req.source == "merra2":
            from hyplan.winds import MERRA2WindField
            wf = MERRA2WindField(
                lat_min=min_lat, lat_max=max_lat,
                lon_min=min_lon, lon_max=max_lon,
                time_start=target_time - datetime.timedelta(hours=2),
                time_end=target_time + datetime.timedelta(hours=2),
                pressure_min_hpa=max(pressure_hpa - 50, 50),
                pressure_max_hpa=min(pressure_hpa + 50, 1000),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown wind source: '{req.source}'")
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("wind-grid", exc)

    # Find closest time and pressure level indices
    target_epoch = target_time.timestamp()
    time_idx = int(np.argmin(np.abs(wf._times - target_epoch)))
    lev_idx = int(np.argmin(np.abs(wf._levs - pressure_hpa)))

    # Extract 2D slices
    u_slice = wf._u_data[time_idx, lev_idx, :, :].tolist()
    v_slice = wf._v_data[time_idx, lev_idx, :, :].tolist()
    lats = wf._lats.tolist()
    lons = wf._lons.tolist()

    nx = len(lons)
    ny = len(lats)
    dx = (lons[-1] - lons[0]) / (nx - 1) if nx > 1 else 0.25
    dy = (lats[-1] - lats[0]) / (ny - 1) if ny > 1 else 0.25

    # Return leaflet-velocity compatible format (pair of header+data objects)
    return [
        {
            "header": {
                "parameterCategory": 2,
                "parameterNumber": 2,
                "parameterNumberName": "eastward_wind",
                "parameterUnit": "m.s-1",
                "lo1": lons[0],
                "la1": lats[-1],
                "lo2": lons[-1],
                "la2": lats[0],
                "dx": dx,
                "dy": dy,
                "nx": nx,
                "ny": ny,
            },
            "data": [val for row in reversed(u_slice) for val in row],
        },
        {
            "header": {
                "parameterCategory": 2,
                "parameterNumber": 3,
                "parameterNumberName": "northward_wind",
                "parameterUnit": "m.s-1",
                "lo1": lons[0],
                "la1": lats[-1],
                "lo2": lons[-1],
                "la2": lats[0],
                "dx": dx,
                "dy": dy,
                "nx": nx,
                "ny": ny,
            },
            "data": [val for row in reversed(v_slice) for val in row],
        },
    ]
