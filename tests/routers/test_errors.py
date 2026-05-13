"""Error classification contract.

These tests guard the shape of error responses across all endpoints.
The frontend's ``getErrorMessage`` and any future programmatic clients
key off ``detail.code``, so changes to the classifier need to be
deliberate.
"""

import pytest

from service.errors import classify


def test_classify_hyplan_value_error_is_400():
    from hyplan.exceptions import HyPlanValueError
    status, code = classify(HyPlanValueError("bad geometry"))
    assert status == 400
    assert code == "hyplan_value_error"


def test_classify_hyplan_type_error_is_400():
    from hyplan.exceptions import HyPlanTypeError
    status, code = classify(HyPlanTypeError("bad type"))
    assert status == 400
    assert code == "hyplan_type_error"


def test_classify_other_hyplan_error_is_400():
    from hyplan.exceptions import HyPlanRuntimeError
    status, code = classify(HyPlanRuntimeError("planning failed"))
    assert status == 400
    assert code == "hyplan_error"


@pytest.mark.parametrize("exc", [ValueError("x"), KeyError("y")])
def test_classify_python_validation_errors_are_400(exc):
    status, code = classify(exc)
    assert status == 400
    assert code == "bad_input"


def test_classify_unexpected_error_is_500():
    status, code = classify(RuntimeError("server bug"))
    assert status == 500
    assert code == "internal_error"


# --- End-to-end: an endpoint surfaces the classified error ------------

def test_generate_pattern_glint_arc_at_night_returns_classified_400(client):
    """An out-of-range solar zenith for glint_arc bubbles up as a 400
    with code ``hyplan_value_error`` and a structured detail body."""
    resp = client.post(
        "/generate-pattern",
        json={
            "campaign_id": "errcamp",
            "campaign_bounds": [-120.5, 35.0, -119.5, 35.5],
            "pattern": "glint_arc",
            "center_lat": 35.0,
            "center_lon": -120.0,
            "heading": 0,
            "altitude_msl_m": 3000,
            "params": {},
            "takeoff_time": "2026-06-15T05:00:00Z",
            "aircraft": "NASA_GV",
            "sensor": "AVIRIS-NG",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert isinstance(body["detail"], dict)
    assert body["detail"]["code"] == "hyplan_value_error"
    assert body["detail"]["operation"] == "generate-pattern"
    assert "Solar zenith" in body["detail"]["message"]


def test_unknown_aircraft_returns_400_legacy_string_detail(client):
    """The aircraft lookup helper raises an HTTPException with a plain
    string detail (not the classifier).  The frontend's
    ``getErrorMessage`` handles both shapes; this test pins the
    legacy contract."""
    resp = client.post(
        "/compute-plan",
        json={
            "sequence": [{
                "kind": "waypoint",
                "latitude": 35.0,
                "longitude": -120.0,
                "altitude_msl_m": 3000,
            }],
            "aircraft": "NotARealAircraft",
            "wind": {"kind": "still_air"},
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    # Plain string detail from _make_aircraft - frontend handles both
    # this and the structured shape.
    assert isinstance(body["detail"], str)
    assert "Unknown aircraft" in body["detail"]
