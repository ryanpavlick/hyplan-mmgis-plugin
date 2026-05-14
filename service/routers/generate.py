"""Flight-line generation from drawn geometry."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from shapely.geometry import shape

from hyplan.flight_box import box_around_polygon, box_around_center_line
from hyplan.instruments import create_sensor
from hyplan.units import ureg

from ..errors import raise_http
from ..schemas import GenerateLinesRequest, GenerateLinesResponse
from ..state import check_revision, get_or_create_campaign, persist_campaign

router = APIRouter()


@router.post("/generate-lines", response_model=GenerateLinesResponse)
def generate_lines(
    req: GenerateLinesRequest,
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
):
    """Generate flight lines from geometry and planning parameters."""
    warnings: list[str] = []

    campaign = get_or_create_campaign(
        req.campaign_id, req.campaign_name, req.campaign_bounds,
    )
    check_revision(campaign, if_match)

    gen = req.generator
    kind = gen.get("kind", "box_around_polygon")
    params = gen.get("params", {})

    sensor_name = params.get("sensor")
    if not sensor_name:
        raise HTTPException(status_code=400, detail="params.sensor is required.")

    try:
        instrument = create_sensor(sensor_name)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid sensor: {exc}")

    altitude_msl = params.get("altitude_msl_m", 3000.0) * ureg.meter
    overlap = params.get("overlap_pct", 20.0)
    azimuth = params.get("azimuth")
    box_name = params.get("box_name", "Box")

    geom = shape(req.geometry.get("geometry", req.geometry))

    try:
        if kind == "box_around_polygon":
            lines = box_around_polygon(
                instrument=instrument,
                altitude_msl=altitude_msl,
                polygon=geom,
                azimuth=azimuth,
                box_name=box_name,
                overlap=overlap,
            )
        elif kind == "box_around_center_line":
            coords = list(geom.coords)
            lat0 = (coords[0][1] + coords[-1][1]) / 2
            lon0 = (coords[0][0] + coords[-1][0]) / 2
            import pymap3d.vincenty
            dist, az12 = pymap3d.vincenty.vdist(
                coords[0][1], coords[0][0], coords[-1][1], coords[-1][0],
            )
            box_length = params.get("box_length_m", float(dist)) * ureg.meter
            box_width = params.get("box_width_m", float(dist) * 0.5) * ureg.meter
            lines = box_around_center_line(
                instrument=instrument,
                altitude_msl=altitude_msl,
                lat0=lat0, lon0=lon0,
                azimuth=float(az12),
                box_length=box_length,
                box_width=box_width,
                box_name=box_name,
                overlap=overlap,
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown generator kind: '{kind}'")
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("generate-lines", exc)

    group_id = campaign.add_flight_lines(
        lines, group_name=box_name, group_type="flight_box",
        generation_params={"kind": kind, **params},
    )
    persist_campaign(campaign)

    return GenerateLinesResponse(
        flight_lines=campaign.flight_lines_to_geojson(),
        groups=campaign.groups,
        summary={"line_count": len(lines), "group_id": group_id},
        warnings=warnings,
        campaign_id=campaign.campaign_id,
        revision=campaign.revision,
    )
