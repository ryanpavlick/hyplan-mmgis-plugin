"""Pydantic request / response schemas for every router.

Kept in one module so that schemas referenced by more than one router
(e.g. :class:`SequenceEntry` from :class:`ComputePlanRequest`) avoid
circular imports.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


# --- Metadata -----------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    hyplan_version: str
    service_version: str = "0.2.0"


# --- Line generation ----------------------------------------------------

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
    geometry: dict   # GeoJSON Feature


class GenerateLinesResponse(BaseModel):
    flight_lines: dict  # GeoJSON FeatureCollection
    groups: list[dict]
    summary: dict
    warnings: list[str]
    campaign_id: str
    revision: int


# --- Plan computation ---------------------------------------------------

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


# --- Optimize-sequence --------------------------------------------------

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


# --- Wind / atmospherics ------------------------------------------------

class WindGridRequest(BaseModel):
    source: str = "gfs"  # "gfs", "gmao", or "merra2"
    bounds: list[float] = Field(..., min_length=4, max_length=4)
    time: str            # ISO 8601 UTC datetime
    altitude_m: float = 3000.0


# --- Export -------------------------------------------------------------

class ExportRequest(BaseModel):
    campaign_id: str
    formats: list[str] = ["kml", "gpx"]


class ExportResponse(BaseModel):
    artifacts: list[dict]
    warnings: list[str]


# --- Manual line editing ------------------------------------------------

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
    operation: str   # rotate, offset_across, offset_along, offset_north_east, reverse, move_endpoint
    params: dict = Field(default_factory=dict)


# --- Patterns -----------------------------------------------------------

class PatternRequest(BaseModel):
    campaign_id: str
    campaign_name: str = "Mission"
    campaign_bounds: list[float] = Field(..., min_length=4, max_length=4)
    pattern: str   # racetrack, rosette, polygon, sawtooth, spiral, glint_arc
    center_lat: float
    center_lon: float
    heading: float = 0.0
    altitude_msl_m: float = 3000.0
    params: dict = Field(default_factory=dict)
    # Required for "glint_arc"; ignored otherwise:
    takeoff_time: Optional[str] = None
    aircraft: Optional[str] = None
    sensor: Optional[str] = None   # for arc swath / glint preview


class DeletePatternRequest(BaseModel):
    campaign_id: str
    pattern_id: str


class ReplacePatternRequest(BaseModel):
    campaign_id: str
    pattern_id: str
    overrides: dict = Field(default_factory=dict)


# --- Analysis overlays --------------------------------------------------

class SwathRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    sensor: str
    altitude_msl_m: float = 3000.0
    # Optional GeoJSON Feature / geometry to score coverage against.
    # When provided, the response includes ``coverage_fraction`` =
    # area(target ∩ unary_union(swaths)) / area(target).
    target_polygon: Optional[dict] = None


class GlintRequest(BaseModel):
    campaign_id: str
    line_ids: list[str]
    sensor: str
    takeoff_time: str  # ISO 8601 UTC
    threshold_deg: float = 25.0
    max_points_per_line: int = 4000


class OptimizeAzimuthRequest(BaseModel):
    lat: float
    lon: float
    altitude_msl_m: float
    sensor: str
    takeoff_time: str  # ISO 8601 UTC
    leg_length_m: float = 15000.0
    step_deg: float = 15.0
    criterion: str = "max_mean"  # "max_mean" | "max_min"


class SolarPositionRequest(BaseModel):
    lat: float
    lon: float
    date: str  # YYYY-MM-DD (interpreted in UTC)
    increment_min: int = 10
