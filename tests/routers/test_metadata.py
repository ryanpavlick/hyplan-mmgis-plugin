"""Metadata endpoints: /, /health, /aircraft, /sensors."""


def test_root_returns_manifest(client):
    """GET / returns a small JSON manifest (pointers to docs / health /
    selectors) so a browser hitting the service base URL doesn't 404."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "HyPlan Service"
    assert "links" in body
    # Each advertised link should resolve.
    for key in ("docs", "health", "aircraft", "sensors"):
        assert key in body["links"]


def test_health_returns_versions(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # hyplan_version is a string (may be "unknown" in unbuilt fresh
    # checkouts; that's fine — we only assert it's present and
    # non-empty).
    assert isinstance(body["hyplan_version"], str)
    assert body["hyplan_version"]
    assert body["service_version"]


def test_aircraft_includes_known_names(client):
    resp = client.get("/aircraft")
    assert resp.status_code == 200
    aircraft = resp.json()["aircraft"]
    # These are stable bundled aircraft in hyplan v1.6+.  The set will
    # grow over time but these specific names should not disappear
    # without a coordinated breaking change.
    assert "NASA_GV" in aircraft
    assert "NASA_ER2" in aircraft
    assert "KingAir350" in aircraft


def test_sensors_includes_known_names(client):
    resp = client.get("/sensors")
    assert resp.status_code == 200
    sensors = resp.json()["sensors"]
    # Same stability contract as aircraft.
    assert "AVIRIS-NG" in sensors
    assert "HyTES" in sensors
