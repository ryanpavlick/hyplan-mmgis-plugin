"""Metadata endpoints: ``/health``, ``/aircraft``, ``/sensors``.

These read static registry information out of HyPlan and serve as the
service's "what can I plan with" surface for the MMGIS frontend.
"""

from __future__ import annotations

import hyplan
from fastapi import APIRouter

from ..schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health():
    # setuptools-scm writes hyplan/_version.py at install time; if hyplan
    # is imported from a source tree that hasn't been built, __version__
    # may be missing.  Fall back to "unknown" rather than 500ing /health.
    return HealthResponse(hyplan_version=getattr(hyplan, "__version__", "unknown"))


@router.get("/aircraft")
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


@router.get("/sensors")
def list_sensors():
    """Return available sensor names."""
    from hyplan.instruments import SENSOR_REGISTRY
    return {"sensors": sorted(SENSOR_REGISTRY.keys())}
