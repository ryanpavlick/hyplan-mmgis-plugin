"""Plan export to KML / GPX + download proxy."""

import os


def _compute(client, cid):
    """Helper: run /compute-plan against the lines fixture so /export has
    a plan to write."""
    r = client.post(
        "/compute-plan",
        json={
            "campaign_id": cid,
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
    assert r.status_code == 200
    return r


def test_export_without_plan_is_400(client, campaign_with_lines):
    resp = client.post(
        "/export",
        json={"campaign_id": campaign_with_lines, "formats": ["kml"]},
    )
    assert resp.status_code == 400
    assert "compute-plan" in resp.json()["detail"]


def test_export_kml_and_gpx(client, campaign_with_lines, tmp_path):
    _compute(client, campaign_with_lines)
    resp = client.post(
        "/export",
        json={"campaign_id": campaign_with_lines, "formats": ["kml", "gpx"]},
    )
    assert resp.status_code == 200
    artifacts = resp.json()["artifacts"]
    formats = {a["format"] for a in artifacts}
    assert formats == {"kml", "gpx"}
    for a in artifacts:
        path = tmp_path / campaign_with_lines / a["filename"]
        assert path.is_file()
        assert path.stat().st_size > 0


def test_download_after_export(client, campaign_with_lines):
    _compute(client, campaign_with_lines)
    artifacts = client.post(
        "/export",
        json={"campaign_id": campaign_with_lines, "formats": ["kml"]},
    ).json()["artifacts"]
    fname = artifacts[0]["filename"]

    resp = client.get(f"/download/{campaign_with_lines}/{fname}")
    assert resp.status_code == 200
    assert resp.content.startswith(b"<?xml")


def test_download_path_traversal_rejected(client, campaign_with_lines):
    resp = client.get(f"/download/{campaign_with_lines}/..%2Fevil")
    # FastAPI's URL parser may give either 400 (our guard) or 404
    # (no such file).  Either is acceptable - the contract is "the
    # path traversal does NOT escape the campaign dir."
    assert resp.status_code in (400, 404)


def test_download_missing_file_is_404(client, campaign_with_lines):
    resp = client.get(f"/download/{campaign_with_lines}/no-such-file.kml")
    assert resp.status_code == 404


def test_unsupported_format_emits_warning(client, campaign_with_lines):
    _compute(client, campaign_with_lines)
    resp = client.post(
        "/export",
        json={"campaign_id": campaign_with_lines, "formats": ["shp"]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["artifacts"] == []
    assert any("shp" in w for w in body["warnings"])
