"""Pattern generation, replacement, deletion, listing."""

import pytest


@pytest.mark.parametrize("pattern_kind,params", [
    ("racetrack", {"leg_length_m": 8000, "n_legs": 2}),
    ("rosette", {"radius_m": 3000, "n_lines": 4}),
    ("polygon", {"radius_m": 3000, "n_sides": 5}),
    ("sawtooth", {
        "altitude_min_m": 1000,
        "altitude_max_m": 5000,
        "leg_length_m": 10000,
        "n_cycles": 2,
    }),
    ("spiral", {
        "altitude_start_m": 500,
        "altitude_end_m": 3000,
        "radius_m": 2000,
        "n_turns": 3,
    }),
])
def test_generate_pattern(client, synthetic_bounds, pattern_kind, params):
    """Every line-based and waypoint-based pattern type generates ok."""
    resp = client.post(
        "/generate-pattern",
        json={
            "campaign_id": f"pat-{pattern_kind}",
            "campaign_bounds": synthetic_bounds,
            "pattern": pattern_kind,
            "center_lat": 35.25,
            "center_lon": -120.0,
            "heading": 90,
            "altitude_msl_m": 5000,
            "params": params,
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["pattern_kind"] == pattern_kind
    assert body["pattern_id"]


def test_generate_glint_arc_at_favorable_time(client, synthetic_bounds):
    """A noonish UTC time over California gives glint_arc favourable
    solar geometry; pattern generation should succeed."""
    resp = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "glint-arc",
            "campaign_bounds": synthetic_bounds,
            "pattern": "glint_arc",
            "center_lat": 35.25,
            "center_lon": -120.0,
            "heading": 0,
            "altitude_msl_m": 5000,
            "params": {"collection_length_m": 30000},
            "takeoff_time": "2026-06-15T19:00:00Z",
            "aircraft": "NASA_GV",
            "sensor": "AVIRIS-NG",
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["pattern_kind"] == "glint_arc"
    # When `sensor` is provided, the response includes the colored arc
    # swath + glint preview - the frontend renders it directly.
    assert "arc_swath" in body
    assert "arc_glint" in body


def test_replace_pattern_propagates_overrides(client, synthetic_bounds):
    """Generating then replacing keeps the same pattern_id but updates params."""
    gen = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "rep1",
            "campaign_bounds": synthetic_bounds,
            "pattern": "racetrack",
            "center_lat": 35.25,
            "center_lon": -120.0,
            "heading": 90,
            "altitude_msl_m": 5000,
            "params": {"leg_length_m": 8000, "n_legs": 2},
        },
    ).json()
    pid = gen["pattern_id"]
    cid = gen["campaign_id"]

    rep = client.post(
        "/replace-pattern",
        json={
            "campaign_id": cid,
            "pattern_id": pid,
            "overrides": {"leg_length_m": 20000, "n_legs": 5},
        },
    )
    assert rep.status_code == 200, rep.json()
    body = rep.json()
    assert body["pattern_id"] == pid              # same id
    assert body["pattern_params"]["leg_length_m"] == 20000
    assert body["pattern_params"]["n_legs"] == 5


def test_delete_pattern_removes_from_campaign(client, synthetic_bounds):
    gen = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "del1",
            "campaign_bounds": synthetic_bounds,
            "pattern": "rosette",
            "center_lat": 35.25,
            "center_lon": -120.0,
            "heading": 0,
            "altitude_msl_m": 5000,
            "params": {"radius_m": 3000, "n_lines": 3},
        },
    ).json()
    pid = gen["pattern_id"]
    cid = gen["campaign_id"]

    listing = client.get(f"/patterns/{cid}").json()
    assert any(p["pattern_id"] == pid for p in listing["patterns"])

    delete = client.post(
        "/delete-pattern", json={"campaign_id": cid, "pattern_id": pid},
    )
    assert delete.status_code == 200

    listing_after = client.get(f"/patterns/{cid}").json()
    assert not any(p["pattern_id"] == pid for p in listing_after["patterns"])


def test_unknown_pattern_kind_is_400(client, synthetic_bounds):
    resp = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "unk",
            "campaign_bounds": synthetic_bounds,
            "pattern": "not-a-pattern",
            "center_lat": 35.25,
            "center_lon": -120.0,
            "heading": 0,
            "altitude_msl_m": 5000,
            "params": {},
        },
    )
    assert resp.status_code == 400
