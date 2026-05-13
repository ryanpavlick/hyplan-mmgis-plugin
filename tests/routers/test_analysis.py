"""Map-analysis overlays: swaths, glint, solar, azimuth sweep."""


def test_generate_swaths_for_two_lines(client, campaign_with_lines):
    resp = client.post(
        "/generate-swaths",
        json={
            "campaign_id": campaign_with_lines,
            "line_ids": ["line_001", "line_002"],
            "sensor": "AVIRIS-NG",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["swaths"]["type"] == "FeatureCollection"
    # gap_overlap analysis runs when n>=2; assert the keys are present.
    assert "total_pairs" in body["gap_overlap"]


def test_compute_glint_at_daytime(client, campaign_with_lines):
    resp = client.post(
        "/compute-glint",
        json={
            "campaign_id": campaign_with_lines,
            "line_ids": ["line_001"],
            "sensor": "AVIRIS-NG",
            "takeoff_time": "2026-06-15T19:00:00Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert not body["sun_below_horizon"]
    assert body["summary"]
    s = body["summary"][0]
    assert s["mean_glint"] is not None
    assert 0 < s["mean_glint"] < 180


def test_compute_glint_at_night_warns(client, campaign_with_lines):
    """Sun below horizon raises a warning but still returns a 200."""
    resp = client.post(
        "/compute-glint",
        json={
            "campaign_id": campaign_with_lines,
            "line_ids": ["line_001"],
            "sensor": "AVIRIS-NG",
            "takeoff_time": "2026-06-15T05:00:00Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["sun_below_horizon"]
    assert any("below the horizon" in w for w in body["warnings"])


def test_solar_position_returns_24h_curve(client):
    resp = client.post(
        "/solar-position",
        json={"lat": 35.25, "lon": -120.0, "date": "2026-06-15", "increment_min": 30},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 24h / 30min = 48 samples; allow some slack for HyPlan's
    # increment semantics.
    assert len(body["time_utc"]) >= 40
    assert body["sunrise_utc"]
    assert body["sunset_utc"]
    # June 15 over California: sunrise is around 12:49 UTC and sunset
    # around 03:11 UTC the *next* UTC day (it's already mid-evening
    # PDT when UTC rolls over).  So we don't expect lexicographic
    # ordering - just that both parse as HH:MM and the daytime arc
    # spans a non-trivial sun-above-horizon stretch.
    import re
    assert re.match(r"^\d{2}:\d{2}$", body["sunrise_utc"])
    assert re.match(r"^\d{2}:\d{2}$", body["sunset_utc"])
    above = sum(1 for e in body["elevation_deg"] if e > 0)
    assert above > 5   # well-clear of "all samples negative"


def test_optimize_azimuth_at_daytime(client):
    resp = client.post(
        "/optimize-azimuth",
        json={
            "lat": 35.25,
            "lon": -120.0,
            "altitude_msl_m": 7000,
            "sensor": "AVIRIS-NG",
            "takeoff_time": "2026-06-15T19:00:00Z",
            "step_deg": 30,   # coarser sweep keeps the test fast
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # 0-360 / 30 deg = 12 headings
    assert len(body["headings"]) == 12
    assert 0 <= body["optimal_azimuth"] < 360
    assert not body["sun_below_horizon"]


def test_optimize_azimuth_invalid_criterion_is_400(client):
    resp = client.post(
        "/optimize-azimuth",
        json={
            "lat": 35.25,
            "lon": -120.0,
            "altitude_msl_m": 7000,
            "sensor": "AVIRIS-NG",
            "takeoff_time": "2026-06-15T19:00:00Z",
            "criterion": "max_unknown",
        },
    )
    assert resp.status_code == 400
