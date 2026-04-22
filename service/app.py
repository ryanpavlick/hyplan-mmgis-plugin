"""HyPlan service — FastAPI bridge between MMGIS and hyplan."""

from __future__ import annotations

import datetime
import logging
import os
import traceback
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import hyplan
from hyplan.aircraft import Aircraft
from hyplan.airports import Airport
from hyplan.campaign import Campaign
from hyplan.flight_box import box_around_polygon, box_around_center_line
from hyplan.flight_line import FlightLine
from hyplan.flight_optimizer import greedy_optimize
from hyplan.instruments import create_sensor
from hyplan.planning import compute_flight_plan
from hyplan.units import ureg
from hyplan.waypoint import Waypoint

from shapely.geometry import shape

logger = logging.getLogger("hyplan-service")

app = FastAPI(title="HyPlan Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    _load_persisted_campaigns()

# In-memory campaign store (keyed by campaign_id)
_campaigns: dict[str, Campaign] = {}
# Most recent computed plan per campaign
_plans: dict[str, Any] = {}

CAMPAIGNS_DIR = os.environ.get("HYPLAN_CAMPAIGNS_DIR", "/tmp/hyplan-campaigns")


def _register_campaign(campaign: Campaign, *extra_keys: str) -> None:
    """Register a campaign in memory under its UUID and any extra keys."""
    _campaigns[campaign.campaign_id] = campaign
    for key in extra_keys:
        if key and key != campaign.campaign_id:
            _campaigns[key] = campaign


def _persist_campaign(campaign: Campaign) -> None:
    """Save campaign to disk."""
    campaign_dir = os.path.join(CAMPAIGNS_DIR, campaign.campaign_id)
    os.makedirs(campaign_dir, exist_ok=True)
    campaign.save(campaign_dir)
    logger.info("Persisted campaign '%s' to %s", campaign.name, campaign_dir)


def _load_persisted_campaigns() -> None:
    """Load all previously saved campaigns from disk on startup."""
    if not os.path.isdir(CAMPAIGNS_DIR):
        return
    for entry in os.listdir(CAMPAIGNS_DIR):
        campaign_dir = os.path.join(CAMPAIGNS_DIR, entry)
        campaign_json = os.path.join(campaign_dir, "campaign.json")
        if os.path.isfile(campaign_json):
            try:
                campaign = Campaign.load(campaign_dir)
                _register_campaign(campaign)
                logger.info("Loaded persisted campaign '%s' (%s)", campaign.name, campaign.campaign_id)
            except Exception as exc:
                logger.warning("Failed to load campaign from %s: %s", campaign_dir, exc)


def _get_or_create_campaign(campaign_id: str, name: str, bounds: list[float]) -> Campaign:
    """Get existing campaign or create a new one."""
    if campaign_id in _campaigns:
        return _campaigns[campaign_id]
    campaign = Campaign(name=name, bounds=tuple(bounds))
    _register_campaign(campaign, campaign_id)
    _persist_campaign(campaign)
    return campaign


def _get_campaign(campaign_id: str) -> Campaign:
    if campaign_id not in _campaigns:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return _campaigns[campaign_id]


def _make_aircraft(name: str) -> Aircraft:
    """Instantiate an aircraft by class name."""
    cls = getattr(hyplan, name, None)
    if cls is None or not isinstance(cls, type) or not issubclass(cls, Aircraft):
        raise HTTPException(status_code=400, detail=f"Unknown aircraft: '{name}'")
    return cls()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    hyplan_version: str
    service_version: str = "0.1.0"


class GeneratorParams(BaseModel):
    sensor: str
    altitude_msl_m: float
    overlap_pct: float = 20.0
    azimuth: Optional[float] = None
    box_name: str = "Box"


class GenerateLinesRequest(BaseModel):
    campaign_id: str
    campaign_name: str = "Mission"
    campaign_bounds: list[float] = Field(..., min_length=4, max_length=4)
    generator: dict  # {"kind": "box_around_polygon", "params": {...}}
    geometry: dict    # GeoJSON Feature


class GenerateLinesResponse(BaseModel):
    flight_lines: dict  # GeoJSON FeatureCollection
    groups: list[dict]
    summary: dict
    warnings: list[str]
    campaign_id: str
    revision: int


class SequenceEntry(BaseModel):
    kind: str  # "line" or "waypoint"
    line_id: Optional[str] = None
    reversed: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_msl_m: Optional[float] = None


class ComputePlanRequest(BaseModel):
    campaign_id: str
    sequence: list[SequenceEntry]
    aircraft: str
    wind: dict = Field(default_factory=lambda: {"kind": "still_air"})
    takeoff_airport: Optional[str] = None
    return_airport: Optional[str] = None
    takeoff_time: Optional[str] = None


class ComputePlanResponse(BaseModel):
    segments: dict  # GeoJSON FeatureCollection
    summary: dict
    warnings: list[str]


class OptimizeRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    aircraft: str
    takeoff_airport: str
    return_airport: Optional[str] = None
    max_endurance: Optional[float] = None
    max_daily_flight_time: Optional[float] = None


class OptimizeResponse(BaseModel):
    proposed_sequence: list[dict]
    total_time: float
    daily_times: list[float]
    lines_covered: int
    lines_skipped: list[str]
    warnings: list[str]


class ExportRequest(BaseModel):
    campaign_id: str
    formats: list[str] = ["kml", "gpx"]


class ExportResponse(BaseModel):
    artifacts: list[dict]
    warnings: list[str]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(hyplan_version=hyplan.__version__)


@app.post("/generate-lines", response_model=GenerateLinesResponse)
def generate_lines(req: GenerateLinesRequest):
    """Generate flight lines from geometry and planning parameters."""
    warnings: list[str] = []

    campaign = _get_or_create_campaign(
        req.campaign_id, req.campaign_name, req.campaign_bounds,
    )

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
            # For center line, geometry should be a LineString
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
        logger.error("generate-lines failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Generation failed: {exc}")

    group_id = campaign.add_flight_lines(
        lines, group_name=box_name, group_type="flight_box",
        generation_params={"kind": kind, **params},
    )
    _persist_campaign(campaign)

    return GenerateLinesResponse(
        flight_lines=campaign.flight_lines_to_geojson(),
        groups=campaign.groups,
        summary={"line_count": len(lines), "group_id": group_id},
        warnings=warnings,
        campaign_id=campaign.campaign_id,
        revision=campaign.revision,
    )


@app.post("/compute-plan", response_model=ComputePlanResponse)
def compute_plan(req: ComputePlanRequest):
    """Compute a flight plan from an ordered sequence."""
    warnings: list[str] = []
    campaign = _get_campaign(req.campaign_id)
    aircraft = _make_aircraft(req.aircraft)

    # Build flight sequence from entries
    lines_by_id = dict(zip(campaign.flight_line_ids, campaign.flight_lines))
    flight_sequence = []

    for entry in req.sequence:
        if entry.kind == "line":
            if entry.line_id not in lines_by_id:
                raise HTTPException(status_code=400, detail=f"Unknown line_id: '{entry.line_id}'")
            line = lines_by_id[entry.line_id]
            if entry.reversed:
                line = line.reverse()
            flight_sequence.append(line)
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
        # Parse takeoff time
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
    except Exception as exc:
        logger.error("compute-plan failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Compute failed: {exc}")

    # Convert plan GeoDataFrame to GeoJSON
    plan_geojson = {
        "type": "FeatureCollection",
        "features": [],
    }
    for _, row in plan.iterrows():
        geom = row.get("geometry")
        if geom is None or geom.is_empty:
            continue
        from shapely.geometry import mapping
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

    # Cache the plan for export
    _plans[req.campaign_id] = plan

    return ComputePlanResponse(
        segments=plan_geojson,
        summary=summary,
        warnings=warnings,
    )


@app.post("/optimize-sequence", response_model=OptimizeResponse)
def optimize_sequence(req: OptimizeRequest):
    """Propose an optimized line ordering."""
    warnings: list[str] = []
    campaign = _get_campaign(req.campaign_id)
    aircraft = _make_aircraft(req.aircraft)

    lines_by_id = dict(zip(campaign.flight_line_ids, campaign.flight_lines))
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
    except Exception as exc:
        logger.error("optimize failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Optimization failed: {exc}")

    # Map optimized FlightLine objects back to line IDs
    optimized_lines = result["flight_sequence"]
    id_by_obj = {id(fl): lid for lid, fl in lines_by_id.items()}

    proposed = []
    for fl in optimized_lines:
        # Check if this is a reversed line
        obj_id = id(fl)
        if obj_id in id_by_obj:
            proposed.append({"kind": "line", "line_id": id_by_obj[obj_id], "reversed": False})
        else:
            # Try matching by endpoints
            for lid, orig in lines_by_id.items():
                if (abs(fl.lat1 - orig.lat2) < 1e-6 and abs(fl.lon1 - orig.lon2) < 1e-6):
                    proposed.append({"kind": "line", "line_id": lid, "reversed": True})
                    break
            else:
                warnings.append(f"Could not map optimized line back to campaign ID")

    return OptimizeResponse(
        proposed_sequence=proposed,
        total_time=result.get("total_time", 0.0),
        daily_times=result.get("daily_times", []),
        lines_covered=result.get("lines_covered", 0),
        lines_skipped=result.get("lines_skipped", []),
        warnings=warnings,
    )


@app.post("/export", response_model=ExportResponse)
def export_plan(req: ExportRequest):
    """Export the most recently computed plan to KML/GPX files."""
    warnings: list[str] = []
    campaign = _get_campaign(req.campaign_id)
    plan = _plans.get(req.campaign_id)

    if plan is None:
        raise HTTPException(status_code=400, detail="No computed plan. Run compute-plan first.")

    campaign_dir = os.path.join(CAMPAIGNS_DIR, campaign.campaign_id)
    os.makedirs(campaign_dir, exist_ok=True)

    # Save campaign state
    campaign.save(campaign_dir)

    import re
    safe_name = re.sub(r'[^\w\-.]', '_', campaign.name).strip('_') or 'flight_plan'

    artifacts = []
    for fmt in req.formats:
        if fmt == "kml":
            try:
                filepath = os.path.join(campaign_dir, f"{safe_name}_flight_plan.kml")
                from hyplan.exports import to_kml
                to_kml(plan, filepath)
                artifacts.append({
                    "format": "kml",
                    "filename": os.path.basename(filepath),
                    "download_url": f"/download/{campaign.campaign_id}/{os.path.basename(filepath)}",
                })
            except Exception as exc:
                warnings.append(f"KML export failed: {exc}")
        elif fmt == "gpx":
            try:
                filepath = os.path.join(campaign_dir, f"{safe_name}_flight_plan.gpx")
                from hyplan.exports import to_gpx
                to_gpx(plan, filepath, mission_name=campaign.name)
                artifacts.append({
                    "format": "gpx",
                    "filename": os.path.basename(filepath),
                    "download_url": f"/download/{campaign.campaign_id}/{os.path.basename(filepath)}",
                })
            except Exception as exc:
                warnings.append(f"GPX export failed: {exc}")
        else:
            warnings.append(f"Unsupported format: '{fmt}'")

    return ExportResponse(artifacts=artifacts, warnings=warnings)


@app.get("/download/{campaign_id}/{filename}")
def download_file(campaign_id: str, filename: str):
    """Download an exported file."""
    # Prevent path traversal
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    filepath = os.path.join(CAMPAIGNS_DIR, campaign_id, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(filepath, filename=filename)


@app.get("/aircraft")
def list_aircraft():
    """Return available aircraft names."""
    from hyplan.aircraft import __all__ as aircraft_all
    from hyplan.aircraft import _models as aircraft_models
    from hyplan.aircraft import Aircraft as AircraftBase
    names = []
    for name in aircraft_all:
        cls = getattr(aircraft_models, name, None)
        if cls and isinstance(cls, type) and issubclass(cls, AircraftBase) and cls is not AircraftBase:
            names.append(name)
    return {"aircraft": sorted(names)}


@app.get("/sensors")
def list_sensors():
    """Return available sensor names."""
    from hyplan.instruments import SENSOR_REGISTRY
    return {"sensors": sorted(SENSOR_REGISTRY.keys())}


@app.post("/campaigns")
def create_campaign(name: str, bounds: list[float]):
    """Create a new campaign."""
    campaign = Campaign(name=name, bounds=tuple(bounds))
    _register_campaign(campaign)
    _persist_campaign(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": campaign.bounds,
        "revision": campaign.revision,
    }


@app.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str):
    """Get campaign state."""
    campaign = _get_campaign(campaign_id)
    return {
        "campaign_id": campaign.campaign_id,
        "name": campaign.name,
        "bounds": campaign.bounds,
        "revision": campaign.revision,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
    }
