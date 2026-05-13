"""FastAPI bridge between the MMGIS HyPlan tool and the core ``hyplan`` library.

This is the service entry point — ``uvicorn service.app:app`` boots
here.  Heavy lifting lives in submodules:

- :mod:`service.state`     — process-wide campaign + plan cache and
                              persistence helpers
- :mod:`service.errors`    — HyPlan-aware exception → HTTP classifier
- :mod:`service.schemas`   — Pydantic request / response models
- :mod:`service.routers.*` — one APIRouter per functional area

The endpoints are organized by concern, not by HTTP method:

- health and selector metadata (``/health``, ``/aircraft``, ``/sensors``)
- campaign geometry generation and flight planning
- map-analysis overlays like wind, swaths, glint, and solar position
- line and pattern mutation helpers used by the interactive MMGIS editor
- export and download endpoints for the latest computed plan

Two pieces of mutable state are important to know about as a reader:

- :data:`service.state._campaigns` stores active :class:`Campaign`
  objects keyed by campaign UUID and any extra aliases used by the
  frontend.
- :data:`service.state._plans` stores the most recently computed plan
  per campaign so the export endpoint can write KML / GPX without
  recomputing.

Campaigns are also persisted to :envvar:`HYPLAN_CAMPAIGNS_DIR` and
reloaded on service startup, so the in-memory state is effectively a
working cache over the saved campaign directories.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import (
    analysis,
    campaigns,
    compute,
    export,
    generate,
    lines,
    metadata,
    patterns,
    tiles,
    wind,
)
from .state import load_persisted_campaigns

logger = logging.getLogger("hyplan-service")

app = FastAPI(title="HyPlan Service", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    load_persisted_campaigns()


# Routers are mounted in the same functional grouping the legacy single
# file used so the resulting OpenAPI surface is byte-stable.
app.include_router(metadata.router)
app.include_router(tiles.router)
app.include_router(wind.router)
app.include_router(generate.router)
app.include_router(compute.router)
app.include_router(export.router)
app.include_router(analysis.router)
app.include_router(lines.router)
app.include_router(patterns.router)
app.include_router(campaigns.router)
