"""FastAPI bridge between the MMGIS HyPlan tool and the core `hyplan` library.

This module is the backend half of the plugin. It translates relatively thin
HTTP requests from the MMGIS frontend into HyPlan campaign mutations,
generation calls, planning runs, and analysis products.

Two pieces of mutable process state are important when reading this file:

- `_campaigns` stores active `Campaign` objects keyed by campaign UUID and any
  extra aliases used by the frontend.
- `_plans` stores the most recently computed flight plan per campaign so the
  export endpoint can write KML/GPX without recomputing.

Campaigns are also persisted to `HYPLAN_CAMPAIGNS_DIR` and reloaded on service
startup, so the in-memory state is effectively a working cache over the saved
campaign directories.

The endpoints are organized in functional groups rather than by HTTP method:

- health and selector metadata (`/health`, `/aircraft`, `/sensors`)
- campaign geometry generation and flight planning
- map-analysis overlays like wind, swaths, glint, and solar position
- line and pattern mutation helpers used by the interactive MMGIS editor
- export and download endpoints for the latest computed plan
"""

from __future__ import annotations

import datetime
import logging
import os
import re
import time
import traceback
from typing import Any, Optional

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

import hyplan
from hyplan.aircraft import Aircraft
from hyplan.airports import Airport
from hyplan.campaign import Campaign
from hyplan.exceptions import HyPlanValueError
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

# Active campaign objects keyed by campaign UUID plus any frontend alias used
# when the browser creates a campaign before it knows the canonical UUID.
_campaigns: dict[str, Campaign] = {}
# Most recent computed GeoDataFrame per campaign. `/export` depends on this
# cache rather than recomputing a plan from browser state.
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
    # Ensure bounds have non-zero extent (add margin if degenerate)
    min_lon, min_lat, max_lon, max_lat = bounds
    if max_lon - min_lon < 0.01:
        min_lon -= 0.5
        max_lon += 0.5
    if max_lat - min_lat < 0.01:
        min_lat -= 0.5
        max_lat += 0.5
    campaign = Campaign(name=name, bounds=(min_lon, min_lat, max_lon, max_lat))
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
    kind: str  # "line", "waypoint", or "pattern"
    line_id: Optional[str] = None
    pattern_id: Optional[str] = None
    reversed: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_msl_m: Optional[float] = None


class ComputePlanRequest(BaseModel):
    campaign_id: Optional[str] = None
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
# Endpoints: health / metadata / planning
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
    # Campaign is optional for waypoint-only sequences
    campaign = None
    if req.campaign_id:
        try:
            campaign = _get_campaign(req.campaign_id)
        except HTTPException:
            if any(e.kind in ("line", "pattern") for e in req.sequence):
                raise  # Need campaign for line/pattern references
    aircraft = _make_aircraft(req.aircraft)
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
                warnings.append("Could not map optimized line back to campaign ID")

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


# --- FAA aeronautical chart tile proxy -------------------------------------
# vfrmap.com serves the current AIRAC cycle's VFR/IFR charts as XYZ/TMS tiles.
# The cycle string (e.g. '20260319') is embedded in the URL path and rolls
# every 28 days, so we scrape it from vfrmap's frontend at runtime and cache
# the result. Tile requests from MMGIS hit /faa-tile/... here, we look up the
# current cycle, forward to vfrmap, and stream the response back.

_VFRMAP_KINDS = {
    "vfrc": "VFR Sectional",
    "sectc": "Sectional (secondary)",
    "helic": "Helicopter Route",
    "ifrlc": "IFR Low Enroute",
    "ehc": "IFR High Enroute",
}
_VFRMAP_CYCLE_TTL_SEC = 3600
_VFRMAP_CYCLE_PATTERN = re.compile(r"""f\s*=\s*['"](\d{8})['"]""")
_vfrmap_cycle_cache: dict[str, Any] = {"cycle": None, "fetched_at": 0.0}


def _get_vfrmap_cycle() -> str:
    now = time.time()
    cached = _vfrmap_cycle_cache["cycle"]
    if cached and (now - _vfrmap_cycle_cache["fetched_at"]) < _VFRMAP_CYCLE_TTL_SEC:
        return cached
    try:
        r = requests.get("https://vfrmap.com/js/map.js", timeout=10)
        r.raise_for_status()
        m = _VFRMAP_CYCLE_PATTERN.search(r.text)
        if m:
            _vfrmap_cycle_cache["cycle"] = m.group(1)
            _vfrmap_cycle_cache["fetched_at"] = now
            return m.group(1)
        logger.warning("vfrmap map.js did not contain AIRAC cycle pattern")
    except Exception as e:
        logger.warning("Failed to fetch vfrmap cycle: %s", e)
    if cached:
        return cached
    raise HTTPException(status_code=503, detail="vfrmap AIRAC cycle unavailable")


@app.get("/faa-tile/{kind}/{z}/{y}/{x}")
def faa_tile(kind: str, z: int, y: int, x: int):
    """Proxy FAA chart tiles from vfrmap.com with auto-refreshed AIRAC cycle.

    MMGIS points its tile layer URL at this endpoint with `tileformat: "tms"`,
    so `y` arrives already in TMS convention (y=0 at bottom), which is what
    vfrmap expects.
    """
    if kind not in _VFRMAP_KINDS:
        raise HTTPException(status_code=404, detail=f"Unknown FAA chart kind: {kind}")
    cycle = _get_vfrmap_cycle()
    upstream = f"https://vfrmap.com/{cycle}/tiles/{kind}/{z}/{y}/{x}.jpg"
    try:
        r = requests.get(upstream, timeout=15)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"vfrmap fetch failed: {e}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="tile not found")
    if not r.ok:
        raise HTTPException(status_code=502, detail=f"vfrmap returned {r.status_code}")
    return Response(
        content=r.content,
        media_type=r.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/imagery-layers")
def imagery_layers():
    """Return pre-configured MMGIS tile layer objects for cloud/satellite imagery
    and FAA aeronautical charts."""
    gibs_base = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best"
    # FAA charts are served via the /faa-tile proxy so the vfrmap AIRAC cycle
    # stays fresh without the operator touching the mission config.
    faa_proxy = "/faa-tile"
    return {
        "layers": [
            {
                "name": "FAA VFR Sectional",
                "type": "tile",
                "url": f"{faa_proxy}/vfrc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 5,
                "maxZoom": 15,
                "maxNativeZoom": 12,
                "attribution": "FAA charts via vfrmap.com",
            },
            {
                "name": "FAA IFR Low Enroute",
                "type": "tile",
                "url": f"{faa_proxy}/ifrlc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 5,
                "maxZoom": 14,
                "maxNativeZoom": 11,
                "attribution": "FAA charts via vfrmap.com",
            },
            {
                "name": "FAA IFR High Enroute",
                "type": "tile",
                "url": f"{faa_proxy}/ehc/{{z}}/{{y}}/{{x}}",
                "tileformat": "tms",
                "visibility": False,
                "minZoom": 4,
                "maxZoom": 13,
                "maxNativeZoom": 10,
                "attribution": "FAA charts via vfrmap.com",
            },
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


# ---------------------------------------------------------------------------
# Endpoints: map analysis overlays
# ---------------------------------------------------------------------------

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
    operation: str  # "rotate", "offset_across", "offset_along", "offset_north_east", "reverse", "move_endpoint"
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
    pattern: str  # "racetrack", "rosette", "polygon", "sawtooth", "spiral", "glint_arc"
    center_lat: float
    center_lon: float
    heading: float = 0.0
    altitude_msl_m: float = 3000.0
    params: dict = Field(default_factory=dict)
    # Required for "glint_arc"; ignored otherwise:
    takeoff_time: Optional[str] = None  # ISO 8601 UTC
    aircraft: Optional[str] = None      # name from /aircraft
    sensor: Optional[str] = None        # name from /sensors; for arc swath/glint preview


class SwathRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    sensor: str
    altitude_msl_m: float = 3000.0


@app.post("/generate-swaths")
def generate_swaths(req: SwathRequest):
    """Generate swath footprint polygons for selected flight lines."""
    from hyplan.swath import generate_swath_polygon, analyze_swath_gaps_overlaps
    from shapely.geometry import mapping as shapely_mapping

    campaign = _get_campaign(req.campaign_id)
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

    # Analyze gaps/overlaps if multiple swaths
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


class GlintRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    sensor: str
    takeoff_time: str  # ISO 8601 UTC, like /compute-plan
    threshold_deg: float = 25.0
    max_points_per_line: int = 4000  # uniform stride-subsample if exceeded


@app.post("/compute-glint")
def compute_glint(req: GlintRequest):
    """Compute per-swath-sample glint angles for selected flight lines.

    Reproduces the visualization from notebooks/glint_analysis.ipynb Cell 10:
    one Point feature per cross-track sample colored by glint_angle.
    """
    from hyplan.glint import compute_glint_vectorized, fraction_exceeding_glint_threshold
    from hyplan.sun import sunpos

    campaign = _get_campaign(req.campaign_id)
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
            logger.error("compute-glint failed for %s: %s", lid, traceback.format_exc())
            warnings.append(f"Glint failed for {lid}: {exc}")
            continue

        # Solar position at the line's midpoint for context in the UI
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

        # Stride-subsample if too dense for browser rendering
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


class OptimizeAzimuthRequest(BaseModel):
    lat: float
    lon: float
    altitude_msl_m: float
    sensor: str
    takeoff_time: str  # ISO 8601 UTC
    leg_length_m: float = 15000.0
    step_deg: float = 15.0
    criterion: str = "max_mean"  # "max_mean" | "max_min"


@app.post("/optimize-azimuth")
def optimize_azimuth(req: OptimizeAzimuthRequest):
    """Sweep flight-line azimuth at a test point and return the heading
    that maximizes glint angle, mirroring notebooks/glint_analysis.ipynb
    Cell 12.

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

    # Solar position at test point + time for context / sun-below-horizon flag
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

    # Pick best heading by chosen criterion
    arr = np.array(mean_glints if req.criterion == "max_mean" else min_glints)
    # NaN-safe argmax
    valid = ~np.isnan(arr)
    if not valid.any():
        raise HTTPException(status_code=500, detail="All sweep samples failed.")
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


class SolarPositionRequest(BaseModel):
    lat: float
    lon: float
    date: str  # YYYY-MM-DD (interpreted in UTC)
    increment_min: int = 10


@app.post("/solar-position")
def solar_position(req: SolarPositionRequest):
    """Return a 24-hour time series of solar position at (lat, lon) on `date`.

    Used by the plugin's "Solar Position" panel to plot solar zenith vs
    UTC time at a user-chosen point.  The full curve (including night) is
    returned so the chart can show sub-horizon values.
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
        logger.error("solar-position failed: %s", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"solar_position_increments failed: {exc}")

    times = df["Time"].tolist()
    elevation = [float(e) for e in df["Elevation"].tolist()]
    azimuth = [float(a) for a in df["Azimuth"].tolist()]
    zenith = [90.0 - e for e in elevation]

    # Linear-interp sunrise/sunset crossings of elevation=0
    def _interp_cross(idx0: int, idx1: int) -> str:
        e0, e1 = elevation[idx0], elevation[idx1]
        if e1 == e0:
            return times[idx0]
        frac = (0.0 - e0) / (e1 - e0)
        # times are HH:MM:SS strings in UTC; convert to minutes-of-day
        h0, m0, s0 = (int(x) for x in times[idx0].split(":"))
        h1, m1, s1 = (int(x) for x in times[idx1].split(":"))
        t0 = h0 * 60 + m0 + s0 / 60.0
        t1 = h1 * 60 + m1 + s1 / 60.0
        # Handle the wrap-around at the end of the day (last sample is 23:50)
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


# ---------------------------------------------------------------------------
# Endpoints: manual line editing
# ---------------------------------------------------------------------------

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
    campaign.add_flight_lines(
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
    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
        "revision": campaign.revision,
    }


@app.post("/delete-line")
def delete_line(req: DeleteLineRequest):
    """Delete a flight line from a campaign (free-standing or pattern leg)."""
    campaign = _get_campaign(req.campaign_id)
    try:
        campaign.remove_line_anywhere(req.line_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "groups": campaign.groups,
        "patterns": campaign.patterns_to_geojson(),
        "revision": campaign.revision,
    }


@app.post("/transform-lines")
def transform_lines(req: TransformLinesRequest):
    """Apply a geometric transform to one or more flight lines.

    Lines may be free-standing or owned by a line-based pattern; either is
    resolved via ``campaign.get_line(...)`` and written back via
    ``campaign.replace_line_anywhere(...)`` so pattern membership is
    preserved.
    """
    campaign = _get_campaign(req.campaign_id)
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
        logger.error("transform failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Transform failed: {exc}")

    _persist_campaign(campaign)
    return {
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
        "transformed": transformed,
        "revision": campaign.revision,
    }


# ---------------------------------------------------------------------------
# Endpoints: pattern generation and mutation
# ---------------------------------------------------------------------------

def _invoke_pattern_generator(kind: str, center: tuple, heading: float,
                              altitude_msl_m: float, params: dict,
                              takeoff_time: Optional[str] = None,
                              aircraft: Optional[str] = None):
    """Dispatch to the right hyplan generator; always returns a Pattern.

    ``takeoff_time`` and ``aircraft`` are required only for ``glint_arc``;
    ignored for other kinds.
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
        ac = _make_aircraft(aircraft)
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


@app.post("/generate-pattern")
def generate_pattern(req: PatternRequest):
    """Generate a flight pattern (racetrack, rosette, polygon, sawtooth, spiral)
    and add it to the campaign as a first-class Pattern."""
    campaign = _get_or_create_campaign(
        req.campaign_id, req.campaign_name, req.campaign_bounds,
    )

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
    except HyPlanValueError as exc:
        # Geometry validation (e.g. solar zenith too high/low for glint_arc)
        # — these are user-actionable, not server bugs.
        raise HTTPException(status_code=400, detail=f"{exc}")
    except Exception as exc:
        logger.error("generate-pattern failed: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Pattern generation failed: {exc}")

    # Give the pattern a friendlier name keyed to its kind count in this campaign
    existing_of_kind = sum(1 for p in campaign.patterns if p.kind == req.pattern)
    pattern.name = f"{req.pattern.capitalize()} {existing_of_kind + 1}"

    campaign.add_pattern(pattern)
    _persist_campaign(campaign)
    payload = _pattern_response_payload(campaign, pattern)

    # For glint_arc, also compute the swath footprint and per-sample glint
    # so the UI can render the colored arc swath in one round-trip.
    if pattern.kind == "glint_arc" and req.sensor:
        try:
            payload.update(_compute_arc_glint_preview(pattern, req.sensor))
        except Exception:
            logger.warning("glint_arc preview failed: %s", traceback.format_exc())
            # Non-fatal — the arc itself is already in the response.

    return payload


def _compute_arc_glint_preview(pattern, sensor_name: str, max_points: int = 4000) -> dict:
    """Build {arc_swath, arc_glint, arc_glint_summary} for a glint_arc Pattern.

    Reconstructs the underlying GlintArc from pattern.params, computes the
    swath footprint polygon and per-sample glint angles, and returns
    GeoJSON-ready dicts for direct rendering by the plugin frontend.
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

    # Footprint polygon
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

    # Per-sample glint
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


class DeletePatternRequest(BaseModel):
    campaign_id: str
    pattern_id: str


@app.post("/delete-pattern")
def delete_pattern(req: DeletePatternRequest):
    """Delete a pattern and all its legs/waypoints from the campaign."""
    campaign = _get_campaign(req.campaign_id)
    try:
        campaign.remove_pattern(req.pattern_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    _persist_campaign(campaign)
    return {
        "campaign_id": campaign.campaign_id,
        "revision": campaign.revision,
        "flight_lines": campaign.flight_lines_to_geojson(),
        "patterns": campaign.patterns_to_geojson(),
    }


class ReplacePatternRequest(BaseModel):
    campaign_id: str
    pattern_id: str
    overrides: dict = Field(default_factory=dict)


@app.post("/replace-pattern")
def replace_pattern(req: ReplacePatternRequest):
    """Regenerate a pattern in place with parameter overrides.

    ``overrides`` is merged into the pattern's stored params (meters/degrees)
    before re-invoking the generator.  The pattern_id is preserved; contained
    flight lines receive fresh line_ids.
    """
    campaign = _get_campaign(req.campaign_id)
    try:
        old = campaign.get_pattern(req.pattern_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    try:
        new_pattern = old.regenerate(**req.overrides)
    except Exception as exc:
        logger.error("replace-pattern regenerate failed: %s", traceback.format_exc())
        raise HTTPException(status_code=400, detail=f"Regenerate failed: {exc}")

    campaign.replace_pattern(req.pattern_id, new_pattern)
    _persist_campaign(campaign)
    return _pattern_response_payload(campaign, new_pattern)


@app.get("/patterns/{campaign_id}")
def list_patterns(campaign_id: str):
    """List all patterns attached to a campaign."""
    campaign = _get_campaign(campaign_id)
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


# ---------------------------------------------------------------------------
# Endpoints: campaign lifecycle / rehydration
# ---------------------------------------------------------------------------

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
        "patterns": campaign.patterns_to_geojson(),
    }
