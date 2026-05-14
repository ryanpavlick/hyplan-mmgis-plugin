"""Metadata endpoints: ``/``, ``/health``, ``/aircraft``, ``/sensors``.

These read static registry information out of HyPlan and serve as the
service's "what can I plan with" surface for the MMGIS frontend.
"""

from __future__ import annotations

import hyplan
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..schemas import HealthResponse

router = APIRouter()


_INDEX_LINKS = [
    ("docs", "/docs", "Interactive Swagger UI — try every endpoint in the browser"),
    ("redoc", "/redoc", "Alternate reference docs"),
    ("openapi", "/openapi.json", "Raw OpenAPI 3 schema"),
    ("health", "/health", "Service + HyPlan version probe"),
    ("aircraft", "/aircraft", "Available aircraft names"),
    ("sensors", "/sensors", "Available sensor names"),
    ("imagery-layers", "/imagery-layers", "FAA charts + NASA GIBS tile layer descriptors"),
]


def _build_manifest() -> dict:
    return {
        "service": "HyPlan Service",
        "version": "0.4.0",
        "hyplan_version": getattr(hyplan, "__version__", "unknown"),
        "links": {key: path for key, path, _ in _INDEX_LINKS},
        "source": "https://github.com/ryanpavlick/hyplan-mmgis-plugin",
    }


def _render_index_html(manifest: dict) -> str:
    rows = "\n".join(
        f'        <li><a href="{path}"><code>{path}</code></a> — {desc}</li>'
        for _, path, desc in _INDEX_LINKS
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HyPlan Service</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 720px;
          margin: 3rem auto; padding: 0 1rem; color: #1f2937; line-height: 1.5; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .versions {{ color: #6b7280; font-size: 0.9rem; margin-bottom: 2rem; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ padding: 0.4rem 0; border-bottom: 1px solid #e5e7eb; }}
  code {{ background: #f3f4f6; padding: 0.1rem 0.4rem; border-radius: 3px; font-size: 0.95em; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .footer {{ margin-top: 2rem; font-size: 0.85rem; color: #6b7280; }}
</style>
</head>
<body>
  <h1>HyPlan Service</h1>
  <div class="versions">
    service {manifest['version']} &middot; hyplan {manifest['hyplan_version']}
  </div>
  <p>FastAPI bridge between MMGIS and the <a href="https://github.com/ryanpavlick/hyplan">HyPlan</a>
     planning library.  Browse the endpoints below or open the
     <a href="/docs">interactive API docs</a>.</p>
  <ul>
{rows}
  </ul>
  <div class="footer">
    Source: <a href="{manifest['source']}">{manifest['source']}</a>
  </div>
</body>
</html>
"""


@router.get("/")
def index(request: Request):
    """Friendly landing page so ``GET /`` doesn't 404.

    Content-negotiated: browsers (Accept: text/html) get a small HTML
    page with clickable links to the docs, health probe, and registry
    endpoints; everything else (curl, the dev/smoke.sh script, programmatic
    clients) gets a JSON manifest with the same information.
    """
    manifest = _build_manifest()
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        return HTMLResponse(_render_index_html(manifest))
    return manifest


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
