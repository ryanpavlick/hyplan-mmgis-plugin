"""/isochrone, /isochrone-concentric, /isochrone-refuel.

The underlying compute_* calls are expensive — these tests use coarse
azimuth resolution and loose distance tolerance so each case runs in a
few seconds even in still-air."""

# A coarse sweep keeps the test budget small.  72 rays at 0.5 nmi
# tolerance is far more precision than these smoke tests need.
COARSE = {
    "azimuth_resolution_deg": 30.0,
    "distance_tolerance_nmi": 5.0,
}


def _airport_start(icao="KSBP"):
    return {"airport": icao}


def test_isochrone_still_air_round_trip(client):
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start(),
        "budget_min": 120.0,
        "mode": "round_trip",
        "wind": {"kind": "still_air"},
        **COARSE,
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 200, resp.text
    fc = resp.json()
    assert fc["type"] == "FeatureCollection"
    # One polygon + N point features.
    polys = [f for f in fc["features"] if f["geometry"]["type"] == "Polygon"]
    points = [f for f in fc["features"] if f["geometry"]["type"] == "Point"]
    assert len(polys) == 1
    assert polys[0]["properties"]["budget_min"] == 120.0
    assert polys[0]["properties"]["mode"] == "round_trip"
    assert len(points) >= 6  # 360/30 = 12 rays
    assert "budget_min" in fc["summary"]


def test_isochrone_waypoint_start(client):
    body = {
        "aircraft": "NASA_GV",
        "start": {
            "latitude": 35.24,
            "longitude": -120.64,
            "altitude_msl_m": 3000.0,
        },
        "budget_min": 60.0,
        "mode": "round_trip",
        **COARSE,
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 200, resp.text


def test_isochrone_waypoint_without_altitude_400s(client):
    body = {
        "aircraft": "NASA_GV",
        "start": {"latitude": 35.24, "longitude": -120.64},
        "budget_min": 60.0,
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 400
    assert "altitude" in resp.json()["detail"].lower()


def test_isochrone_unknown_airport_400s(client):
    body = {
        "aircraft": "NASA_GV",
        "start": {"airport": "ZZZZ"},
        "budget_min": 60.0,
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 400


def test_isochrone_gridded_wind_rejected_with_clear_error(client):
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start(),
        "budget_min": 60.0,
        "wind": {"kind": "gfs"},
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 400
    assert "gfs" in resp.json()["detail"].lower()


def test_isochrone_constant_wind(client):
    """Constant 30-kt wind from 270° should produce an asymmetric
    boundary vs still-air — but we only sanity-check status here;
    the shape diff is exercised in hyplan's own test suite."""
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start(),
        "budget_min": 90.0,
        "mode": "round_trip",
        "wind": {"kind": "constant", "speed_kt": 30.0, "direction_deg": 270.0},
        **COARSE,
    }
    resp = client.post("/isochrone", json=body)
    assert resp.status_code == 200, resp.text


def test_isochrone_concentric_multiple_budgets(client):
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start(),
        "budget_min": 0.0,                # ignored by concentric
        "budgets_min": [60.0, 120.0, 180.0],
        "mode": "round_trip",
        **COARSE,
    }
    resp = client.post("/isochrone-concentric", json=body)
    assert resp.status_code == 200, resp.text
    fc = resp.json()
    polys = [f for f in fc["features"] if f["geometry"]["type"] == "Polygon"]
    assert len(polys) == 3
    budgets = sorted(p["properties"]["budget_min"] for p in polys)
    assert budgets == [60.0, 120.0, 180.0]


def test_isochrone_concentric_empty_budgets_rejected(client):
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start(),
        "budget_min": 0.0,
        "budgets_min": [],
    }
    resp = client.post("/isochrone-concentric", json=body)
    # Caught by pydantic min_length=1.
    assert resp.status_code == 422


def test_isochrone_refuel_happy_path(client):
    """Refuel sweeps three itinerary templates per ray — coarse
    resolution keeps it under a few seconds in CI."""
    body = {
        "aircraft": "NASA_GV",
        "start": _airport_start("KSBP"),
        "budget_min": 240.0,                  # per-cycle sortie
        "flight_day_budget_min": 600.0,
        "mode": "return_safe",
        "return_destination": _airport_start("KSBP"),
        "refuel_airports": ["KLAS"],
        "refuel_time_min": 45.0,
        "azimuth_resolution_deg": 60.0,       # 6 rays
        "distance_tolerance_nmi": 10.0,
    }
    resp = client.post("/isochrone-refuel", json=body)
    assert resp.status_code == 200, resp.text
    fc = resp.json()
    assert any(f["geometry"]["type"] == "Polygon" for f in fc["features"])
