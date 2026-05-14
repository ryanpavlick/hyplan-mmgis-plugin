"""Campaign lifecycle: create / read / export / import bundle."""


def test_export_returns_bundle_with_expected_shape(client, campaign_with_lines):
    resp = client.get(f"/campaigns/{campaign_with_lines}/export")
    assert resp.status_code == 200, resp.json()
    bundle = resp.json()
    assert bundle["format"] == "hyplan-mmgis-plugin-campaign"
    assert bundle["format_version"] == 1
    assert bundle["campaign_id"] == campaign_with_lines
    assert bundle["revision"] >= 1
    files = bundle["files"]
    # campaign.json + flight_lines/* + (no patterns yet on the
    # campaign_with_lines fixture, which only has the box).
    assert "campaign.json" in files
    assert "flight_lines/all_lines.geojson" in files
    # KML / GPX / KMZ artifacts MUST NOT be in the bundle.
    assert not any(
        path.endswith((".kml", ".gpx", ".kmz")) for path in files
    )


def test_import_with_fresh_uuid_does_not_collide(
    client, campaign_with_lines, synthetic_bounds,
):
    """Default import assigns a new campaign_id so re-importing a
    bundle into a live service doesn't overwrite the existing one."""
    bundle = client.get(f"/campaigns/{campaign_with_lines}/export").json()
    original_id = bundle["campaign_id"]

    resp = client.post("/campaigns/import", json={"bundle": bundle})
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["campaign_id"] != original_id          # fresh UUID
    assert body["name"] == bundle["name"]
    assert len(body["flight_lines"]["features"]) == \
           len(bundle["files"]["flight_lines/all_lines.geojson"]["features"])


def test_import_with_replace_keeps_bundle_id(client, campaign_with_lines):
    bundle = client.get(f"/campaigns/{campaign_with_lines}/export").json()
    original_id = bundle["campaign_id"]

    resp = client.post(
        "/campaigns/import", json={"bundle": bundle, "replace": True},
    )
    assert resp.status_code == 200
    assert resp.json()["campaign_id"] == original_id


def test_import_with_name_override(client, campaign_with_lines):
    bundle = client.get(f"/campaigns/{campaign_with_lines}/export").json()
    resp = client.post(
        "/campaigns/import",
        json={"bundle": bundle, "name": "Renamed on Import"},
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed on Import"


def test_import_rejects_wrong_format(client):
    resp = client.post(
        "/campaigns/import",
        json={"bundle": {"format": "not-a-real-format", "format_version": 1, "files": {}}},
    )
    assert resp.status_code == 400
    assert "Unrecognized bundle format" in resp.json()["detail"]


def test_import_rejects_future_format_version(client, campaign_with_lines):
    bundle = client.get(f"/campaigns/{campaign_with_lines}/export").json()
    bundle["format_version"] = 999
    resp = client.post("/campaigns/import", json={"bundle": bundle})
    assert resp.status_code == 400
    assert "format_version" in resp.json()["detail"]


def test_import_rejects_path_traversal(client, campaign_with_lines):
    bundle = client.get(f"/campaigns/{campaign_with_lines}/export").json()
    bundle["files"]["../escaped.json"] = {"x": 1}
    resp = client.post("/campaigns/import", json={"bundle": bundle})
    assert resp.status_code == 400
    assert "Invalid bundle file path" in resp.json()["detail"]


def test_import_rejects_empty_files(client):
    resp = client.post(
        "/campaigns/import",
        json={
            "bundle": {
                "format": "hyplan-mmgis-plugin-campaign",
                "format_version": 1,
                "files": {},
            }
        },
    )
    assert resp.status_code == 400
    assert "files" in resp.json()["detail"].lower()


def test_round_trip_preserves_lines_and_patterns(client, synthetic_bounds, synthetic_polygon):
    # Build a richer campaign: a line box + a racetrack pattern.
    cid = client.post(
        "/generate-lines",
        json={
            "campaign_id": "rt-src",
            "campaign_bounds": synthetic_bounds,
            "generator": {
                "kind": "box_around_polygon",
                "params": {
                    "sensor": "AVIRIS-NG",
                    "altitude_msl_m": 7000,
                    "overlap_pct": 20,
                    "azimuth": 0,
                    "box_name": "RT",
                },
            },
            "geometry": synthetic_polygon,
        },
    ).json()["campaign_id"]
    client.post(
        "/generate-pattern",
        json={
            "campaign_id": cid,
            "campaign_bounds": synthetic_bounds,
            "pattern": "racetrack",
            "center_lat": 35.25, "center_lon": -120.0, "heading": 90,
            "altitude_msl_m": 5000,
            "params": {"leg_length_m": 8000, "n_legs": 2},
        },
    )

    original = client.get(f"/campaigns/{cid}").json()
    bundle = client.get(f"/campaigns/{cid}/export").json()
    imported = client.post("/campaigns/import", json={"bundle": bundle}).json()

    # Same counts after round-trip.
    assert len(imported["flight_lines"]["features"]) == \
           len(original["flight_lines"]["features"])
    assert len(imported["patterns"]["features"]) == \
           len(original["patterns"]["features"])
    # Imported campaign is independent — fresh UUID, separate state.
    assert imported["campaign_id"] != cid
