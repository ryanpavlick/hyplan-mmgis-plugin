"""Shared pytest fixtures for the service test suite.

The service has process-wide state (``service.state._campaigns`` /
``service.state._plans``) and a campaign-persistence directory pulled
from ``HYPLAN_CAMPAIGNS_DIR`` at import time.  We isolate every test
function by:

1. Pointing ``HYPLAN_CAMPAIGNS_DIR`` at a per-test tmp dir **before**
   ``service.app`` is imported (session-scoped autouse fixture).
2. Clearing ``service.state._campaigns`` / ``_plans`` between tests
   (function-scoped autouse fixture).
3. Patching :data:`service.state.CAMPAIGNS_DIR` to the per-test tmp
   path so persistence writes land somewhere disposable.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Set the campaigns dir *before* service.app is imported.  This is a
# module-level side effect so it runs on conftest import, ahead of
# any test discovery that touches service modules.
_SESSION_TMP = tempfile.mkdtemp(prefix="hyplan-tests-")
os.environ["HYPLAN_CAMPAIGNS_DIR"] = _SESSION_TMP


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Reset in-memory campaign state + redirect persistence to tmp_path.

    Autouse so every test starts with an empty ``_campaigns`` /
    ``_plans`` and writes to its own disposable directory.
    """
    from service import state

    state._campaigns.clear()
    state._plans.clear()
    monkeypatch.setattr(state, "CAMPAIGNS_DIR", str(tmp_path))
    # Some submodules grabbed CAMPAIGNS_DIR by reference at import
    # time (e.g. service.routers.export uses `from ..state import
    # CAMPAIGNS_DIR`).  Patch those names too so /export and
    # /download don't write to the stale session tmp dir.
    from service.routers import export as _export_mod
    monkeypatch.setattr(_export_mod, "CAMPAIGNS_DIR", str(tmp_path))


@pytest.fixture
def client():
    """A FastAPI ``TestClient`` bound to the real app."""
    from fastapi.testclient import TestClient
    from service.app import app

    return TestClient(app)


# --- Geometry / data fixtures -----------------------------------------

@pytest.fixture
def synthetic_polygon() -> dict:
    """A ~30 nm box near San Luis Obispo - matches the smoke tests."""
    return {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [-120.3, 35.1],
                [-119.7, 35.1],
                [-119.7, 35.4],
                [-120.3, 35.4],
                [-120.3, 35.1],
            ]],
        },
    }


@pytest.fixture
def synthetic_bounds() -> list[float]:
    """Bounds matching :func:`synthetic_polygon`."""
    return [-120.5, 35.0, -119.5, 35.5]


@pytest.fixture
def campaign_with_lines(client, synthetic_polygon, synthetic_bounds):
    """Create a campaign + a small box of flight lines; return ``campaign_id``.

    Many tests need a campaign with at least a couple of lines already
    in it (compute-plan, transforms, swaths, glint).  This fixture
    creates one via the real ``/generate-lines`` endpoint and returns
    the resulting UUID.
    """
    resp = client.post(
        "/generate-lines",
        json={
            "campaign_id": "test-camp",
            "campaign_bounds": synthetic_bounds,
            "generator": {
                "kind": "box_around_polygon",
                "params": {
                    "sensor": "AVIRIS-NG",
                    "altitude_msl_m": 7000,
                    "overlap_pct": 20,
                    "azimuth": 0,
                    "box_name": "Test",
                },
            },
            "geometry": synthetic_polygon,
        },
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["campaign_id"]
