"""Plan computation and route optimization: /compute-plan, /optimize-sequence."""

import pytest


def test_compute_plan_still_air_with_two_lines(client, campaign_with_lines):
    resp = client.post(
        "/compute-plan",
        json={
            "campaign_id": campaign_with_lines,
            "sequence": [
                {"kind": "line", "line_id": "line_001"},
                {"kind": "line", "line_id": "line_002"},
            ],
            "aircraft": "NASA_GV",
            "wind": {"kind": "still_air"},
            "takeoff_airport": "KSBP",
            "return_airport": "KSBP",
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    summary = body["summary"]
    # 2 flight lines plus connecting transit + climb/descent segments.
    # Pin loose bounds rather than an exact count - segment composition
    # depends on hyplan's planner internals.
    assert summary["segments"] >= 2
    assert summary["total_distance_nm"] > 0
    assert summary["total_time_min"] > 0
    assert summary["flight_line_segments"] >= 2


def test_compute_plan_with_constant_wind(client, campaign_with_lines):
    resp = client.post(
        "/compute-plan",
        json={
            "campaign_id": campaign_with_lines,
            "sequence": [
                {"kind": "line", "line_id": "line_001"},
                {"kind": "line", "line_id": "line_002"},
            ],
            "aircraft": "NASA_GV",
            "wind": {"kind": "constant", "speed_kt": 30, "direction_deg": 270},
            "takeoff_airport": "KSBP",
            "return_airport": "KSBP",
        },
    )
    assert resp.status_code == 200
    # Constant west wind: total flight time should differ from still
    # air for the same sequence.  We don't assert the direction of the
    # difference (depends on segment headings) - just that the call
    # path works.
    assert resp.json()["summary"]["total_time_min"] > 0


def test_compute_plan_unknown_line_id_is_400(client, campaign_with_lines):
    resp = client.post(
        "/compute-plan",
        json={
            "campaign_id": campaign_with_lines,
            "sequence": [{"kind": "line", "line_id": "line_999"}],
            "aircraft": "NASA_GV",
            "wind": {"kind": "still_air"},
        },
    )
    assert resp.status_code == 400
    assert "line_999" in resp.json()["detail"]


def test_compute_plan_waypoint_only_sequence_no_campaign(client):
    """Waypoint-only sequences don't require a campaign."""
    resp = client.post(
        "/compute-plan",
        json={
            "sequence": [
                {"kind": "waypoint", "latitude": 35.0, "longitude": -120.0, "altitude_msl_m": 3000},
                {"kind": "waypoint", "latitude": 35.5, "longitude": -120.0, "altitude_msl_m": 3000},
            ],
            "aircraft": "NASA_GV",
            "wind": {"kind": "still_air"},
        },
    )
    # Two waypoints + still air should at least not crash.  Some
    # plan-engine failures here are legitimate (no airports, no
    # transit climb path); the contract is "if it fails, it's a
    # classified 4xx, not an opaque 500."
    if resp.status_code != 200:
        body = resp.json()
        assert resp.status_code == 400, body
        assert isinstance(body["detail"], (str, dict))


def test_unknown_sequence_kind_is_400(client, campaign_with_lines):
    resp = client.post(
        "/compute-plan",
        json={
            "campaign_id": campaign_with_lines,
            "sequence": [{"kind": "not-a-kind"}],
            "aircraft": "NASA_GV",
            "wind": {"kind": "still_air"},
        },
    )
    assert resp.status_code == 400


# --- /optimize-sequence -----------------------------------------------

@pytest.mark.parametrize("aircraft", ["NASA_GV", "NASA_ER2"])
def test_optimize_sequence_returns_proposal(client, campaign_with_lines, aircraft):
    resp = client.post(
        "/optimize-sequence",
        json={
            "campaign_id": campaign_with_lines,
            "line_ids": ["line_001", "line_002", "line_003"],
            "aircraft": aircraft,
            "takeoff_airport": "KSBP",
            "return_airport": "KSBP",
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["lines_covered"] >= 1
    assert isinstance(body["proposed_sequence"], list)
    assert body["total_time"] >= 0


def test_optimize_sequence_unknown_line_id_is_400(client, campaign_with_lines):
    resp = client.post(
        "/optimize-sequence",
        json={
            "campaign_id": campaign_with_lines,
            "line_ids": ["line_999"],
            "aircraft": "NASA_GV",
            "takeoff_airport": "KSBP",
        },
    )
    assert resp.status_code == 400
