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


class WindGridRequest(BaseModel):
    source: str = "gfs"  # "gfs", "gmao", or "merra2"
    bounds: list[float] = Field(..., min_length=4, max_length=4)  # [min_lon, min_lat, max_lon, max_lat]
    time: str  # ISO 8601 UTC datetime
    altitude_m: float = 3000.0


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


@app.post("/wind-grid")
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
        logger.error("wind-grid fetch failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Wind data fetch failed: {exc}")

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


@app.get("/imagery-layers")
def imagery_layers():
    """Return pre-configured MMGIS tile layer objects for cloud/satellite imagery."""
    gibs_base = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    return {
        "layers": [
            {
                "name": "VIIRS SNPP True Color",
                "type": "tile",
                "url": f"{gibs_base}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/{{time}}/GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.jpg",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 9,
                "maxNativeZoom": 9,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
            {
                "name": "MODIS Terra True Color",
                "type": "tile",
                "url": f"{gibs_base}/MODIS_Terra_CorrectedReflectance_TrueColor/default/{{time}}/GoogleMapsCompatible_Level9/{{z}}/{{y}}/{{x}}.jpg",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 9,
                "maxNativeZoom": 9,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
            {
                "name": "GOES-East GeoColor",
                "type": "tile",
                "url": f"{gibs_base}/GOES-East_ABI_GeoColor/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "GOES-West GeoColor",
                "type": "tile",
                "url": f"{gibs_base}/GOES-West_ABI_GeoColor/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "Himawari-9 Band 3",
                "type": "tile",
                "url": f"{gibs_base}/Himawari_AHI_Band3_Red_Visible/default/{{time}}/GoogleMapsCompatible_Level7/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 7,
                "maxNativeZoom": 7,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%dT%H:%M:%SZ"},
            },
            {
                "name": "MODIS Cloud Fraction",
                "type": "tile",
                "url": f"{gibs_base}/MODIS_Terra_Cloud_Fraction_Day/default/{{time}}/GoogleMapsCompatible_Level6/{{z}}/{{y}}/{{x}}.png",
                "tileformat": "wmts",
                "visibility": False,
                "minZoom": 1,
                "maxZoom": 6,
                "maxNativeZoom": 6,
                "time": {"enabled": True, "type": "requery", "format": "%Y-%m-%d"},
            },
        ]
    }


class AddLineRequest(BaseModel):
    campaign_id: Optional[str] = None
    lat1: float
    lon1: float
    lat2: float
    lon2: float
    altitude_msl_m: float = 3000.0
    site_name: Optional[str] = None


class EditLineRequest(BaseModel):
    campaign_id: str
    line_id: str
    lat1: Optional[float] = None
    lon1: Optional[float] = None
    lat2: Optional[float] = None
    lon2: Optional[float] = None
    altitude_msl_m: Optional[float] = None
    site_name: Optional[str] = None


class DeleteLineRequest(BaseModel):
    campaign_id: str
    line_id: str


class AddWaypointRequest(BaseModel):
    campaign_id: str
    latitude: float
    longitude: float
    altitude_msl_m: float = 3000.0
    heading: float = 0.0
    name: Optional[str] = None


class TransformLinesRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    operation: str  # "rotate", "offset_across", "offset_along", "offset_north_east", "move_endpoint"
    params: dict = Field(default_factory=dict)
    # rotate: {"angle_deg": float}
    # offset_across: {"distance_m": float}
    # offset_along: {"start_m": float, "end_m": float}
    # offset_north_east: {"north_m": float, "east_m": float}
    # move_endpoint: {"line_id": str, "endpoint": "start"|"end", "lat": float, "lon": float}


class PatternRequest(BaseModel):
    campaign_id: str
    campaign_name: str = "Mission"
    campaign_bounds: list[float] = Field(..., min_length=4, max_length=4)
    pattern: str  # "racetrack", "rosette", "polygon", "sawtooth", "spiral"
    center_lat: float
    center_lon: float
    heading: float = 0.0
    altitude_msl_m: float = 3000.0
    params: dict = Field(default_factory=dict)


@app.post("/add-line")
def add_line(req: AddLineRequest):
    """Add a single flight line to a campaign."""
    if not req.campaign_id:
        # Create a new campaign from the line's midpoint
        mid_lat = (req.lat1 + req.lat2) / 2
        mid_lon = (req.lon1 + req.lon2) / 2
        bounds = [mid_lon - 1, mid_lat - 1, mid_lon + 1, mid_lat + 1]
        campaign = _get_or_create_campaign(
            'campaign-' + str(int(datetime.datetime.now().timestamp())),
            req.site_name or 'Mission',
            bounds,
        )
    else:
        campaign = _get_campaign(req.campaign_id)
    line = FlightLine.from_endpoints(
        req.lat1, req.lon1, req.lat2, req.lon2,
        altitude_msl=req.altitude_msl_m * ureg.meter,
        site_name=req.site_name or f"Line {len(campaign.flight_line_ids) + 1}",
    )
    group_id = campaign.add_flight_lines(
        [line], group_name=req.site_name or "Manual", group_type="manual",
    )
    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "added_line_id": campaign.flight_line_ids[-1],
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
    }


@app.post("/edit-line")
def edit_line(req: EditLineRequest):
    """Edit an existing flight line's endpoints, altitude, or name."""
    campaign = _get_campaign(req.campaign_id)

    # Get existing line
    lines_by_id = dict(zip(campaign.flight_line_ids, campaign.flight_lines))
    if req.line_id not in lines_by_id:
        raise HTTPException(status_code=400, detail=f"Unknown line_id: '{req.line_id}'")

    old = lines_by_id[req.line_id]
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
    campaign.replace_flight_line(req.line_id, new_line)
    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "revision": campaign.revision,
    }


@app.post("/delete-line")
def delete_line(req: DeleteLineRequest):
    """Delete a flight line from a campaign."""
    campaign = _get_campaign(req.campaign_id)
    campaign.remove_flight_line(req.line_id)
    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "revision": campaign.revision,
    }


@app.post("/transform-lines")
def transform_lines(req: TransformLinesRequest):
    """Apply a geometric transform to one or more flight lines."""
    campaign = _get_campaign(req.campaign_id)
    lines_by_id = dict(zip(campaign.flight_line_ids, campaign.flight_lines))
    params = req.params
    transformed = 0

    try:
        if req.operation == "rotate":
            angle = float(params.get("angle_deg", 0))
            for lid in req.line_ids:
                if lid not in lines_by_id:
                    continue
                new_line = lines_by_id[lid].rotate_around_midpoint(angle)
                campaign.replace_flight_line(lid, new_line)
                transformed += 1

        elif req.operation == "offset_across":
            distance = float(params.get("distance_m", 0)) * ureg.meter
            for lid in req.line_ids:
                if lid not in lines_by_id:
                    continue
                new_line = lines_by_id[lid].offset_across(distance)
                campaign.replace_flight_line(lid, new_line)
                transformed += 1

        elif req.operation == "offset_along":
            start_m = float(params.get("start_m", 0)) * ureg.meter
            end_m = float(params.get("end_m", 0)) * ureg.meter
            for lid in req.line_ids:
                if lid not in lines_by_id:
                    continue
                new_line = lines_by_id[lid].offset_along(start_m, end_m)
                campaign.replace_flight_line(lid, new_line)
                transformed += 1

        elif req.operation == "offset_north_east":
            north = float(params.get("north_m", 0)) * ureg.meter
            east = float(params.get("east_m", 0)) * ureg.meter
            for lid in req.line_ids:
                if lid not in lines_by_id:
                    continue
                new_line = lines_by_id[lid].offset_north_east(north, east)
                campaign.replace_flight_line(lid, new_line)
                transformed += 1

        elif req.operation == "move_endpoint":
            lid = params.get("line_id", req.line_ids[0] if req.line_ids else "")
            endpoint = params.get("endpoint", "start")
            lat = float(params.get("lat", 0))
            lon = float(params.get("lon", 0))
            if lid not in lines_by_id:
                raise HTTPException(status_code=400, detail=f"Unknown line_id: '{lid}'")
            old = lines_by_id[lid]
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
            campaign.replace_flight_line(lid, new_line)
            transformed += 1

        else:
            raise HTTPException(status_code=400, detail=f"Unknown operation: '{req.operation}'")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("transform failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Transform failed: {exc}")

    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "transformed": transformed,
        "revision": campaign.revision,
    }


@app.post("/generate-pattern")
def generate_pattern(req: PatternRequest):
    """Generate a flight pattern (racetrack, rosette, polygon, sawtooth, spiral)."""
    from hyplan.flight_patterns import (
        racetrack, rosette, polygon as poly_pattern, sawtooth, spiral,
    )

    campaign = _get_or_create_campaign(
        req.campaign_id, req.campaign_name, req.campaign_bounds,
    )

    center = (req.center_lat, req.center_lon)
    altitude = req.altitude_msl_m * ureg.meter
    params = req.params

    try:
        if req.pattern == "racetrack":
            waypoints = racetrack(
                center=center,
                heading=req.heading,
                altitude=altitude,
                leg_length=params.get("leg_length_m", 10000) * ureg.meter,
                n_legs=params.get("n_legs", 1),
                offset=params.get("offset_m", 0) * ureg.meter if params.get("offset_m") else 0 * ureg.meter,
            )
        elif req.pattern == "rosette":
            waypoints = rosette(
                center=center,
                heading=req.heading,
                altitude=altitude,
                radius=params.get("radius_m", 5000) * ureg.meter,
                n_lines=params.get("n_lines", 3),
            )
        elif req.pattern == "polygon":
            waypoints = poly_pattern(
                center=center,
                heading=req.heading,
                altitude=altitude,
                radius=params.get("radius_m", 5000) * ureg.meter,
                n_sides=params.get("n_sides", 4),
            )
        elif req.pattern == "sawtooth":
            waypoints = sawtooth(
                center=center,
                heading=req.heading,
                altitude_min=params.get("altitude_min_m", 1000) * ureg.meter,
                altitude_max=params.get("altitude_max_m", 3000) * ureg.meter,
                leg_length=params.get("leg_length_m", 10000) * ureg.meter,
                n_cycles=params.get("n_cycles", 2),
            )
        elif req.pattern == "spiral":
            waypoints = spiral(
                center=center,
                heading=req.heading,
                altitude_start=params.get("altitude_start_m", 500) * ureg.meter,
                altitude_end=params.get("altitude_end_m", 3000) * ureg.meter,
                radius=params.get("radius_m", 3000) * ureg.meter,
                n_turns=params.get("n_turns", 3),
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown pattern: '{req.pattern}'")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("generate-pattern failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pattern generation failed: {exc}")

    # Convert waypoints to GeoJSON for display
    features = []
    coords = []
    for i, wp in enumerate(waypoints):
        coords.append([wp.longitude, wp.latitude])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [wp.longitude, wp.latitude]},
            "properties": {
                "name": wp.name or f"WP{i+1}",
                "altitude_msl": wp.altitude_msl.m_as(ureg.meter) if wp.altitude_msl else None,
                "heading": wp.heading,
                "index": i,
            },
        })

    # Add the track as a LineString
    if len(coords) >= 2:
        features.insert(0, {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"name": req.pattern, "type": "pattern_track"},
        })

    return {
        "pattern": req.pattern,
        "waypoints": {
            "type": "FeatureCollection",
            "features": features,
        },
        "waypoint_count": len(waypoints),
        "campaign_id": campaign.campaign_id,
    }


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
