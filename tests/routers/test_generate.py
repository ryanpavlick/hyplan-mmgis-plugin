"""Flight-line generation: /generate-lines."""


def test_box_around_polygon_produces_lines(client, synthetic_polygon, synthetic_bounds):
    resp = client.post(
        "/generate-lines",
        json={
            "campaign_id": "g1",
            "campaign_bounds": synthetic_bounds,
            "generator": {
                "kind": "box_around_polygon",
                "params": {
                    "sensor": "AVIRIS-NG",
                    "altitude_msl_m": 7000,
                    "overlap_pct": 20,
                    "azimuth": 0,
                    "box_name": "Box1",
                },
            },
            "geometry": synthetic_polygon,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # The box is ~30 nm of polygon, AVIRIS-NG at FL230 with 20% overlap
    # yields ~10+ parallel lines.  Don't pin an exact count (it varies
    # with hyplan internal geometry) - assert "more than a handful."
    assert len(body["flight_lines"]["features"]) >= 4
    assert body["revision"] == 1
    assert body["campaign_id"]


def test_generate_lines_persists_campaign(
    client, synthetic_polygon, synthetic_bounds,
):
    """The campaign survives the /generate-lines call and is queryable
    via /campaigns/{id} (i.e. it's been written to the SQLite store)."""
    resp = client.post(
        "/generate-lines",
        json={
            "campaign_id": "g2",
            "campaign_bounds": synthetic_bounds,
            "generator": {
                "kind": "box_around_polygon",
                "params": {
                    "sensor": "AVIRIS-NG",
                    "altitude_msl_m": 5000,
                    "overlap_pct": 20,
                    "box_name": "Box",
                },
            },
            "geometry": synthetic_polygon,
        },
    )
    cid = resp.json()["campaign_id"]
    # In-memory state has it.
    get_resp = client.get(f"/campaigns/{cid}")
    assert get_resp.status_code == 200
    # And the persisted store knows about it (lightweight summary list).
    listing = client.get("/campaigns").json()["campaigns"]
    assert any(c["campaign_id"] == cid for c in listing)


def test_generate_lines_missing_sensor_is_400(
    client, synthetic_polygon, synthetic_bounds,
):
    resp = client.post(
        "/generate-lines",
        json={
            "campaign_id": "g3",
            "campaign_bounds": synthetic_bounds,
            "generator": {"kind": "box_around_polygon", "params": {}},
            "geometry": synthetic_polygon,
        },
    )
    assert resp.status_code == 400
    assert "sensor" in resp.json()["detail"].lower()


def test_unknown_generator_kind_is_400(client, synthetic_polygon, synthetic_bounds):
    resp = client.post(
        "/generate-lines",
        json={
            "campaign_id": "g4",
            "campaign_bounds": synthetic_bounds,
            "generator": {
                "kind": "not-a-real-generator",
                "params": {"sensor": "AVIRIS-NG", "altitude_msl_m": 5000},
            },
            "geometry": synthetic_polygon,
        },
    )
    assert resp.status_code == 400
