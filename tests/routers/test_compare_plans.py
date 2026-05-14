"""/compare-plans — segment-by-segment diff of two computed plans."""


def _seg(name, segment_type, distance, time_to_segment,
         start_alt=3000.0, end_alt=3000.0):
    """Build one plan Feature for tests; mirrors what /compute-plan emits."""
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
        "properties": {
            "segment_type": segment_type,
            "segment_name": name,
            "distance": distance,
            "time_to_segment": time_to_segment,
            "start_altitude": start_alt,
            "end_altitude": end_alt,
        },
    }


def _plan(features):
    return {"type": "FeatureCollection", "features": features}


def test_identical_plans_zero_delta(client):
    a = _plan([
        _seg("climb", "climb", 10.0, 5.0),
        _seg("L01", "flight_line", 30.0, 12.0),
    ])
    resp = client.post(
        "/compare-plans",
        json={"plan_a": a, "plan_b": a},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["delta_distance_nm"] == 0
    assert body["summary"]["delta_time_min"] == 0
    assert body["summary"]["matched"] == 2
    assert body["summary"]["added"] == 0
    assert body["summary"]["removed"] == 0


def test_same_shape_different_wind_reports_deltas(client):
    """Same sequence, slightly different distances and times — the
    canonical 'what changed when I added wind' comparison."""
    a = _plan([
        _seg("climb", "climb", 10.0, 5.0),
        _seg("L01", "flight_line", 30.0, 12.0),
    ])
    b = _plan([
        _seg("climb", "climb", 10.0, 5.5),               # +0.5 min
        _seg("L01", "flight_line", 30.0, 13.2),          # +1.2 min
    ])
    resp = client.post(
        "/compare-plans",
        json={"plan_a": a, "plan_b": b, "label_a": "still_air", "label_b": "gfs"},
    )
    body = resp.json()
    assert body["label_a"] == "still_air"
    assert body["label_b"] == "gfs"
    assert body["summary"]["matched"] == 2
    assert abs(body["summary"]["delta_time_min"] - 1.7) < 1e-6
    assert body["summary"]["delta_distance_nm"] == 0
    # Per-segment deltas surface in the segments list.
    by_name = {s["segment_name"]: s for s in body["segments"]}
    assert by_name["climb"]["delta_time_min"] == 0.5
    assert abs(by_name["L01"]["delta_time_min"] - 1.2) < 1e-6


def test_added_segment_when_b_is_longer(client):
    a = _plan([_seg("climb", "climb", 10.0, 5.0)])
    b = _plan([
        _seg("climb", "climb", 10.0, 5.0),
        _seg("transit", "transit", 50.0, 18.0),     # extra in B
    ])
    body = client.post(
        "/compare-plans", json={"plan_a": a, "plan_b": b},
    ).json()
    assert body["summary"]["added"] == 1
    assert body["summary"]["removed"] == 0
    assert body["summary"]["delta_segments"] == 1
    extras = [s for s in body["segments"] if s["status"] == "added"]
    assert len(extras) == 1
    assert extras[0]["segment_name"] == "transit"
    assert "a" not in extras[0]    # added rows carry B only


def test_removed_segment_when_a_is_longer(client):
    a = _plan([
        _seg("climb", "climb", 10.0, 5.0),
        _seg("transit", "transit", 50.0, 18.0),
    ])
    b = _plan([_seg("climb", "climb", 10.0, 5.0)])
    body = client.post(
        "/compare-plans", json={"plan_a": a, "plan_b": b},
    ).json()
    assert body["summary"]["added"] == 0
    assert body["summary"]["removed"] == 1
    assert body["summary"]["delta_segments"] == -1
    drops = [s for s in body["segments"] if s["status"] == "removed"]
    assert len(drops) == 1
    assert drops[0]["segment_name"] == "transit"
    assert "b" not in drops[0]


def test_empty_plans_round_trip(client):
    """Two empty FeatureCollections diff to all-zero."""
    empty = _plan([])
    body = client.post(
        "/compare-plans", json={"plan_a": empty, "plan_b": empty},
    ).json()
    assert body["summary"] == {
        "matched": 0,
        "added": 0,
        "removed": 0,
        "segments_a": 0,
        "segments_b": 0,
        "delta_segments": 0,
        "delta_distance_nm": 0.0,
        "delta_time_min": 0.0,
    }
    assert body["segments"] == []


def test_non_list_features_400s(client):
    bogus = {"type": "FeatureCollection", "features": "oops"}
    resp = client.post(
        "/compare-plans", json={"plan_a": bogus, "plan_b": _plan([])},
    )
    assert resp.status_code == 400


def test_end_to_end_via_compute_plan(client, campaign_with_lines):
    """The intended use case: compute the same sequence twice with
    different wind and compare.  Even with deterministic still-air
    HyPlan output, the matched count + zero deltas exercise the
    real /compute-plan -> /compare-plans pipe."""
    body = {
        "campaign_id": campaign_with_lines,
        "sequence": [
            {"kind": "line", "line_id": "line_001"},
            {"kind": "line", "line_id": "line_002"},
        ],
        "aircraft": "NASA_GV",
        "wind": {"kind": "still_air"},
        "takeoff_airport": "KSBP",
        "return_airport": "KSBP",
    }
    plan_a = client.post("/compute-plan", json=body).json()["segments"]
    plan_b = client.post("/compute-plan", json=body).json()["segments"]

    diff = client.post(
        "/compare-plans", json={"plan_a": plan_a, "plan_b": plan_b},
    ).json()
    # Same sequence + wind → identical plans, all matched, zero delta.
    assert diff["summary"]["added"] == 0
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["matched"] == len(plan_a["features"])
    assert diff["summary"]["delta_distance_nm"] == 0
    assert diff["summary"]["delta_time_min"] == 0
