"""Per-line editing: add, edit, delete, transform."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, HTTPException

from hyplan.flight_line import FlightLine
from hyplan.units import ureg

from ..errors import raise_http
from ..schemas import (
    AddLineRequest,
    DeleteLineRequest,
    EditLineRequest,
    ResolveRelativeRequest,
    TransformLinesRequest,
)
from ..state import get_campaign, get_or_create_campaign, persist_campaign

router = APIRouter()


@router.post("/add-line")
def add_line(req: AddLineRequest):
    """Add a single flight line to a campaign."""
    if not req.campaign_id:
        # Create a new campaign from the line's midpoint
        mid_lat = (req.lat1 + req.lat2) / 2
        mid_lon = (req.lon1 + req.lon2) / 2
        bounds = [mid_lon - 1, mid_lat - 1, mid_lon + 1, mid_lat + 1]
        campaign = get_or_create_campaign(
            'campaign-' + str(int(datetime.datetime.now().timestamp())),
            req.site_name or 'Mission',
            bounds,
        )
    else:
        campaign = get_campaign(req.campaign_id)
    line = FlightLine.from_endpoints(
        req.lat1, req.lon1, req.lat2, req.lon2,
        altitude_msl=req.altitude_msl_m * ureg.meter,
        site_name=req.site_name or f"Line {len(campaign.flight_line_ids) + 1}",
    )
    campaign.add_flight_lines(
        [line], group_name=req.site_name or "Manual", group_type="manual",
    )
    persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "added_line_id": campaign.flight_line_ids[-1],
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
    }


@router.post("/resolve-relative")
def resolve_relative(req: ResolveRelativeRequest):
    """Resolve a point at a geodesic offset from an anchor.

    Returns ``{latitude, longitude}``.  The frontend uses this for
    the "place this endpoint 100 nm @ 270° from waypoint X" workflow
    — the user enters anchor + bearing + distance, the backend does
    the Vincenty math (via :meth:`Waypoint.relative_to`), the
    resolved coordinates flow into the existing /add-line or
    /edit-line endpoint with no per-feature "*-relative" variant.
    """
    from hyplan.waypoint import Waypoint
    try:
        wp = Waypoint.relative_to(
            anchor=(req.anchor_lat, req.anchor_lon),
            bearing=req.bearing_deg,
            distance=req.distance_m * ureg.meter,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise_http("resolve-relative", exc)

    return {
        "latitude": wp.latitude,
        "longitude": wp.longitude,
        "anchor_lat": req.anchor_lat,
        "anchor_lon": req.anchor_lon,
        "bearing_deg": req.bearing_deg,
        "distance_m": req.distance_m,
    }


@router.post("/edit-line")
def edit_line(req: EditLineRequest):
    """Edit an existing flight line's endpoints, altitude, or name."""
    campaign = get_campaign(req.campaign_id)

    try:
        old = campaign.get_line(req.line_id)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Unknown line_id: '{req.line_id}'")

    lat1 = req.lat1 if req.lat1 is not None else old.lat1
    lon1 = req.lon1 if req.lon1 is not None else old.lon1
    lat2 = req.lat2 if req.lat2 is not None else old.lat2
    lon2 = req.lon2 if req.lon2 is not None else old.lon2
    alt = (req.altitude_msl_m * ureg.meter) if req.altitude_msl_m is not None else old.altitude_msl
    name = req.site_name if req.site_name is not None else old.site_name

    new_line = FlightLine.from_endpoints(
        lat1, lon1, lat2, lon2,
        altitude_msl=alt, site_name=name,
        site_description=old.site_description,
        investigator=old.investigator,
    )
    campaign.replace_line_anywhere(req.line_id, new_line)
    persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
        "revision": campaign.revision,
    }


@router.post("/delete-line")
def delete_line(req: DeleteLineRequest):
    """Delete a flight line from a campaign (free-standing or pattern leg)."""
    campaign = get_campaign(req.campaign_id)
    try:
        campaign.remove_line_anywhere(req.line_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "patterns": campaign.patterns_to_geojson(),
        "revision": campaign.revision,
    }


@router.post("/transform-lines")
def transform_lines(req: TransformLinesRequest):
    """Apply a geometric transform to one or more flight lines.

    Lines may be free-standing or owned by a line-based pattern; either
    is resolved via :meth:`Campaign.get_line` and written back via
    :meth:`Campaign.replace_line_anywhere` so pattern membership is
    preserved.
    """
    campaign = get_campaign(req.campaign_id)
    params = req.params
    transformed = 0

    def _get(lid: str):
        try:
            return campaign.get_line(lid)
        except Exception:
            return None

    try:
        if req.operation == "rotate":
            angle = float(params.get("angle_deg", 0))
            for lid in req.line_ids:
                old = _get(lid)
                if old is None:
                    continue
                campaign.replace_line_anywhere(lid, old.rotate_around_midpoint(angle))
                transformed += 1

        elif req.operation == "offset_across":
            distance = float(params.get("distance_m", 0)) * ureg.meter
            for lid in req.line_ids:
                old = _get(lid)
                if old is None:
                    continue
                campaign.replace_line_anywhere(lid, old.offset_across(distance))
                transformed += 1

        elif req.operation == "offset_along":
            start_m = float(params.get("start_m", 0)) * ureg.meter
            end_m = float(params.get("end_m", 0)) * ureg.meter
            for lid in req.line_ids:
                old = _get(lid)
                if old is None:
                    continue
                campaign.replace_line_anywhere(lid, old.offset_along(start_m, end_m))
                transformed += 1

        elif req.operation == "offset_north_east":
            north = float(params.get("north_m", 0)) * ureg.meter
            east = float(params.get("east_m", 0)) * ureg.meter
            for lid in req.line_ids:
                old = _get(lid)
                if old is None:
                    continue
                campaign.replace_line_anywhere(lid, old.offset_north_east(north, east))
                transformed += 1

        elif req.operation == "reverse":
            for lid in req.line_ids:
                old = _get(lid)
                if old is None:
                    continue
                campaign.replace_line_anywhere(lid, old.reverse())
                transformed += 1

        elif req.operation == "move_endpoint":
            lid = params.get("line_id", req.line_ids[0] if req.line_ids else "")
            endpoint = params.get("endpoint", "start")
            lat = float(params.get("lat", 0))
            lon = float(params.get("lon", 0))
            old = _get(lid)
            if old is None:
                raise HTTPException(status_code=400, detail=f"Unknown line_id: '{lid}'")
            if endpoint == "start":
                new_line = FlightLine.from_endpoints(
                    lat, lon, old.lat2, old.lon2,
                    altitude_msl=old.altitude_msl, site_name=old.site_name,
                    site_description=old.site_description, investigator=old.investigator,
                )
            else:
                new_line = FlightLine.from_endpoints(
                    old.lat1, old.lon1, lat, lon,
                    altitude_msl=old.altitude_msl, site_name=old.site_name,
                    site_description=old.site_description, investigator=old.investigator,
                )
            campaign.replace_line_anywhere(lid, new_line)
            transformed += 1

        else:
            raise HTTPException(status_code=400, detail=f"Unknown operation: '{req.operation}'")

    except HTTPException:
        raise
    except Exception as exc:
        raise_http("transform-lines", exc)

    persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
        "transformed": transformed,
        "revision": campaign.revision,
    }
