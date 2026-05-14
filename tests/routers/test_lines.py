"""Per-line editing: add, edit, delete, transform."""


def test_add_line_to_existing_campaign(client, campaign_with_lines):
    resp = client.post(
        "/add-line",
        json={
            "campaign_id": campaign_with_lines,
            "lat1": 35.20, "lon1": -120.00,
            "lat2": 35.30, "lon2": -120.00,
            "altitude_msl_m": 3000,
            "site_name": "Manual1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["added_line_id"]
    assert body["revision"] >= 2   # generate-lines set it to 1


def test_edit_line_replaces_endpoints(client, campaign_with_lines):
    resp = client.post(
        "/edit-line",
        json={
            "campaign_id": campaign_with_lines,
            "line_id": "line_001",
            "lat1": 35.15, "lon1": -120.10,
            "lat2": 35.35, "lon2": -120.10,
            "site_name": "edited",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    features = body["flight_lines"]["features"]
    edited = next(f for f in features if f["id"] == "line_001")
    coords = edited["geometry"]["coordinates"]
    # Endpoint replacement is exact - assert the new endpoints round-trip.
    assert abs(coords[0][0] - (-120.10)) < 1e-3
    assert abs(coords[0][1] - 35.15) < 1e-3 or abs(coords[0][1] - 35.35) < 1e-3


def test_delete_line_removes_it(client, campaign_with_lines):
    before = client.get(f"/campaigns/{campaign_with_lines}").json()
    before_ids = [f["id"] for f in before["flight_lines"]["features"]]
    assert "line_001" in before_ids

    resp = client.post(
        "/delete-line",
        json={"campaign_id": campaign_with_lines, "line_id": "line_001"},
    )
    assert resp.status_code == 200

    after_ids = [f["id"] for f in resp.json()["flight_lines"]["features"]]
    assert "line_001" not in after_ids


def test_transform_rotate(client, campaign_with_lines):
    resp = client.post(
        "/transform-lines",
        json={
            "campaign_id": campaign_with_lines,
            "operation": "rotate",
            "line_ids": ["line_001", "line_002"],
            "params": {"angle_deg": 15},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["transformed"] == 2


def test_transform_offset_north_east(client, campaign_with_lines):
    resp = client.post(
        "/transform-lines",
        json={
            "campaign_id": campaign_with_lines,
            "operation": "offset_north_east",
            "line_ids": ["line_001"],
            "params": {"north_m": 500, "east_m": 200},
        },
    )
    assert resp.status_code == 200
    assert resp.json()["transformed"] == 1


def test_transform_reverse(client, campaign_with_lines):
    """Reverse should swap the line's endpoints in the GeoJSON output."""
    before = client.get(f"/campaigns/{campaign_with_lines}").json()
    before_l1 = next(
        f for f in before["flight_lines"]["features"] if f["id"] == "line_001"
    )
    before_coords = before_l1["geometry"]["coordinates"]

    resp = client.post(
        "/transform-lines",
        json={
            "campaign_id": campaign_with_lines,
            "operation": "reverse",
            "line_ids": ["line_001"],
            "params": {},
        },
    )
    assert resp.status_code == 200
    after_l1 = next(
        f for f in resp.json()["flight_lines"]["features"] if f["id"] == "line_001"
    )
    after_coords = after_l1["geometry"]["coordinates"]
    # Endpoints swap.
    assert after_coords[0] == before_coords[-1]
    assert after_coords[-1] == before_coords[0]


def test_resolve_relative_north_1km(client):
    """1 km due north of (35, -120) lands at ~(35.009, -120)."""
    resp = client.post(
        "/resolve-relative",
        json={
            "anchor_lat": 35.0,
            "anchor_lon": -120.0,
            "bearing_deg": 0,    # north
            "distance_m": 1000,
        },
    )
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert abs(body["latitude"] - 35.009) < 0.001    # 1/111 deg
    assert abs(body["longitude"] - (-120.0)) < 0.001  # purely north
    # Echoed inputs are present.
    assert body["bearing_deg"] == 0
    assert body["distance_m"] == 1000


def test_resolve_relative_east_1km(client):
    """1 km due east of (35, -120).  At 35° latitude one degree of
    longitude is ~91 km, so 1 km east ≈ 0.011° longitude east."""
    resp = client.post(
        "/resolve-relative",
        json={
            "anchor_lat": 35.0,
            "anchor_lon": -120.0,
            "bearing_deg": 90,
            "distance_m": 1000,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert abs(body["latitude"] - 35.0) < 0.001        # purely east
    assert body["longitude"] > -120.0                   # moved east
    assert 0.008 < (body["longitude"] - (-120.0)) < 0.013


def test_resolve_relative_then_add_line_composes(client):
    """Resolve a relative point, then use it as one endpoint of /add-line.
    Demonstrates the intended frontend composition pattern."""
    pt = client.post(
        "/resolve-relative",
        json={"anchor_lat": 35.25, "anchor_lon": -120.0, "bearing_deg": 45, "distance_m": 5000},
    ).json()

    add = client.post(
        "/add-line",
        json={
            "lat1": 35.25, "lon1": -120.0,
            "lat2": pt["latitude"], "lon2": pt["longitude"],
            "altitude_msl_m": 5000,
            "site_name": "relative-test",
        },
    )
    assert add.status_code == 200, add.json()
    assert add.json()["added_line_id"]


def test_unknown_operation_is_400(client, campaign_with_lines):
    resp = client.post(
        "/transform-lines",
        json={
            "campaign_id": campaign_with_lines,
            "operation": "not-real",
            "line_ids": ["line_001"],
            "params": {},
        },
    )
    assert resp.status_code == 400
