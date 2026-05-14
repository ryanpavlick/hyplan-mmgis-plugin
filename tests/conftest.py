"""Shared pytest fixtures for the service test suite.

The service has process-wide state (``service.state._campaigns`` /
``service.state._plans``) and a SQLite-backed campaign store whose
path is pulled from ``HYPLAN_CAMPAIGNS_DB`` at import time.  We
isolate every test function by:

1. Pointing ``HYPLAN_CAMPAIGNS_DB`` and ``HYPLAN_CAMPAIGNS_DIR`` at
   a session-level tmp dir **before** ``service.app`` is imported
   (module-level side effect so it runs on conftest import).
2. Clearing ``service.state._campaigns`` / ``_plans`` between
   tests (function-scoped autouse fixture).
3. Re-initializing the SQLite store at a per-test ``tmp_path`` and
   pointing ``service.state.CAMPAIGNS_DIR`` (used by /export for
   KML/GPX scratch files) at the same per-test path.

Net effect: every test starts from an empty store with a fresh
in-memory cache, and writes land in a disposable directory pytest
cleans up.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Set the campaigns dir + db path *before* service.app is imported.
# This is a module-level side effect so it runs on conftest import,
# ahead of any test discovery that touches service modules.
_SESSION_TMP = tempfile.mkdtemp(prefix="hyplan-tests-")
os.environ["HYPLAN_CAMPAIGNS_DIR"] = _SESSION_TMP
os.environ["HYPLAN_CAMPAIGNS_DB"] = os.path.join(_SESSION_TMP, "campaigns.sqlite")


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Reset in-memory campaign state + redirect persistence to tmp_path.

    Autouse so every test starts with an empty ``_campaigns`` /
    ``_plans`` and writes to its own disposable directory.
    """
    from service import state, store

    state._campaigns.clear()
    state._plans.clear()

    monkeypatch.setattr(state, "CAMPAIGNS_DIR", str(tmp_path))
    # ``service.routers.export`` grabbed CAMPAIGNS_DIR by reference at
    # import time; patch the alias so /export and /download write to
    # the per-test path, not the session tmp dir.
    from service.routers import export as _export_mod
    monkeypatch.setattr(_export_mod, "CAMPAIGNS_DIR", str(tmp_path))

    # Fresh SQLite db per test.  init_store closes any prior
    # connection automatically.
    db_path = str(tmp_path / "campaigns.sqlite")
    monkeypatch.setattr(state, "CAMPAIGNS_DB", db_path)
    store.init_store(db_path)


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
