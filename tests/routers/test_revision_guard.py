"""Concurrent-edit guard: ``If-Match: <revision>`` precondition on writes.

The contract:

- Missing / empty ``If-Match`` header → no precondition (legacy
  clients keep writing).
- ``If-Match`` integer matching ``campaign.revision`` → write proceeds.
- ``If-Match`` integer mismatching the server's revision → ``409
  Conflict`` with a structured detail including the actual server
  revision (so the UI can refresh and retry).
- ``If-Match`` not parseable as int → ``400`` with ``code=bad_if_match``.
"""


def _add_a_line(client, campaign_id, if_match=None):
    headers = {"If-Match": str(if_match)} if if_match is not None else {}
    return client.post(
        "/add-line",
        json={
            "campaign_id": campaign_id,
            "lat1": 35.20, "lon1": -120.00,
            "lat2": 35.30, "lon2": -120.00,
            "altitude_msl_m": 3000,
            "site_name": "guard-test",
        },
        headers=headers,
    )


def test_no_if_match_is_unconditional(client, campaign_with_lines):
    """Legacy clients (no header) write without precondition."""
    resp = _add_a_line(client, campaign_with_lines)
    assert resp.status_code == 200, resp.json()


def test_if_match_matching_revision_writes(client, campaign_with_lines):
    """Fresh-from-/campaigns/{id} revision matches server → write."""
    rev = client.get(f"/campaigns/{campaign_with_lines}").json()["revision"]
    resp = _add_a_line(client, campaign_with_lines, if_match=rev)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["revision"] == rev + 1


def test_if_match_stale_revision_409s(client, campaign_with_lines):
    """Stale revision (from before another client's write) → 409."""
    # Bump the server revision by writing without If-Match.
    _add_a_line(client, campaign_with_lines)
    # Now try a write with the OLD revision (1) — should fail.
    resp = _add_a_line(client, campaign_with_lines, if_match=1)
    assert resp.status_code == 409
    body = resp.json()
    assert body["detail"]["code"] == "revision_mismatch"
    assert body["detail"]["client_revision"] == 1
    assert body["detail"]["server_revision"] >= 2


def test_if_match_garbage_400s(client, campaign_with_lines):
    resp = _add_a_line(client, campaign_with_lines, if_match="not-a-number")
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["code"] == "bad_if_match"


def test_if_match_on_transform_lines(client, campaign_with_lines):
    """The guard applies to /transform-lines too."""
    rev = client.get(f"/campaigns/{campaign_with_lines}").json()["revision"]
    # Stale revision is rejected.
    resp = client.post(
        "/transform-lines",
        json={
            "campaign_id": campaign_with_lines,
            "operation": "rotate",
            "line_ids": ["line_001"],
            "params": {"angle_deg": 5},
        },
        headers={"If-Match": str(rev - 1)},
    )
    assert resp.status_code == 409


def test_if_match_on_generate_pattern(client, campaign_with_lines, synthetic_bounds):
    """Same guard applies to /generate-pattern."""
    rev = client.get(f"/campaigns/{campaign_with_lines}").json()["revision"]
    resp = client.post(
        "/generate-pattern",
        json={
            "campaign_id": campaign_with_lines,
            "campaign_bounds": synthetic_bounds,
            "pattern": "racetrack",
            "center_lat": 35.25, "center_lon": -120.0, "heading": 90,
            "altitude_msl_m": 5000,
            "params": {"leg_length_m": 8000, "n_legs": 2},
        },
        headers={"If-Match": str(rev)},   # matching → ok
    )
    assert resp.status_code == 200
    # Another write at the SAME (now stale) revision should 409.
    stale = client.post(
        "/generate-pattern",
        json={
            "campaign_id": campaign_with_lines,
            "campaign_bounds": synthetic_bounds,
            "pattern": "rosette",
            "center_lat": 35.25, "center_lon": -120.0, "heading": 0,
            "altitude_msl_m": 5000,
            "params": {"radius_m": 3000, "n_lines": 3},
        },
        headers={"If-Match": str(rev)},
    )
    assert stale.status_code == 409


def test_if_match_on_transform_pattern(client, synthetic_bounds):
    """Guard applies to /transform-pattern."""
    gen = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "rev-tx",
            "campaign_bounds": synthetic_bounds,
            "pattern": "racetrack",
            "center_lat": 35.25, "center_lon": -120.0, "heading": 90,
            "altitude_msl_m": 5000,
            "params": {"leg_length_m": 8000, "n_legs": 2},
        },
    ).json()
    cid, pid, rev = gen["campaign_id"], gen["pattern_id"], gen["revision"]

    # Stale revision → 409
    resp = client.post(
        "/transform-pattern",
        json={
            "campaign_id": cid, "pattern_id": pid,
            "operation": "translate",
            "params": {"north_m": 1000, "east_m": 0},
        },
        headers={"If-Match": str(rev - 1)},
    )
    assert resp.status_code == 409
