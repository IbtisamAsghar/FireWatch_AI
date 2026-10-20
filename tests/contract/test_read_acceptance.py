"""Phase 1.6 transport contracts over migrated PostgreSQL and all planned reads."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from firewatch.api.routes.reads import health, operator, router
from firewatch.schemas.api import NOTICE, ErrorEnvelope
from scripts.demo_data import IDS, read_requests

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("operation,path", list(read_requests().items()))
def test_every_read_has_a_successful_typed_demo_response(
    demo_client, demo_settings, operation, path
):
    headers = (
        {"Authorization": "Bearer " + demo_settings.admin_api_token.get_secret_value()}
        if operation == "get_job"
        else {}
    )
    response = demo_client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    route = next(
        route
        for route in [*demo_client.app.routes, *router.routes, *health.routes, *operator.routes]
        if isinstance(route, APIRoute) and route.operation_id == operation
    )
    parsed = TypeAdapter(route.response_model).validate_json(response.content)
    assert parsed is not None and UUID(response.headers["X-Request-ID"])
    if path.startswith("/api/v1/"):
        assert response.json()["schema_version"] == "api-v1"
        assert response.json()["research_notice"] == NOTICE


def test_timestamps_units_nulls_and_geojson_are_explicit(demo_client):
    detail = demo_client.get(f"/api/v1/hotspots/{IDS['live-critical']}").json()
    assert detail["features"] == {"frp_max_mw": 42, "temperature_c": 32, "wind_ms": 5}
    assert detail["units"] == {"frp_max_mw": "MW", "temperature_c": "degC", "wind_ms": "m/s"}
    assert detail["as_of"] == "2025-06-11T12:00:00Z"
    assert datetime.fromisoformat(detail["as_of"].replace("Z", "+00:00")).tzinfo == UTC
    assert detail["hotspot"]["escalation_probability"] is None
    assert detail["hotspot"]["score_kind"] == "baseline"
    assert detail["evidence"]["payload"]["synthetic"] is True
    missing = demo_client.get(f"/api/v1/hotspots/{IDS['live-low']}").json()
    assert (
        missing["features"]["frp_max_mw"] is None and missing["missing_flags"]["frp_max_mw"] is True
    )
    assert missing["hotspot"]["frp_max_mw"] is None
    geo = demo_client.get(f"/api/v1/hotspots.geojson?run_id={IDS['live']}").json()
    assert geo["features"][0]["geometry"]["coordinates"] == [71, 34]
    assert geo["features"][0]["properties"]["escalation_probability"] is None


def test_pagination_summary_and_replay_scope_stay_consistent(demo_client):
    live = demo_client.get(f"/api/v1/hotspots?run_id={IDS['live']}&limit=1").json()
    second = demo_client.get(f"/api/v1/hotspots?run_id={IDS['live']}&limit=1&offset=1").json()
    assert live["total"] == second["total"] == 2
    assert live["limit"] == 1 and second["offset"] == 1
    assert live["items"][0]["snapshot_id"] == str(IDS["live-critical"])
    assert second["items"][0]["snapshot_id"] == str(IDS["live-low"])
    replay = demo_client.get(f"/api/v1/hotspots?run_id={IDS['replay']}").json()
    assert replay["mode"] == "replay" and replay["run_id"] == str(IDS["replay"])
    assert [row["snapshot_id"] for row in replay["items"]] == [str(IDS["replay-high"])]
    assert replay["freshness"]["reference_kind"] == "historical_cutoff"
    assert replay["freshness"]["weather_age_hours"] == 2
    assert (
        demo_client.get(
            f"/api/v1/hotspots/{IDS['live-critical']}?run_id={IDS['replay']}"
        ).status_code
        == 404
    )
    assert (
        demo_client.get(f"/api/v1/alerts/{IDS['alert']}?run_id={IDS['replay']}").status_code == 404
    )
    query = f"run_id={IDS['live']}&priority=CRITICAL&bbox=70,33,72,35"
    assert demo_client.get("/api/v1/hotspots?" + query).json()["total"] == 1
    assert demo_client.get("/api/v1/summary?" + query).json()["total"] == 1
    assert demo_client.get("/api/v1/hotspots.geojson?" + query).json()["total"] == 1
    for suffix in ("hotspots", "summary", "hotspots.geojson"):
        empty = demo_client.get(f"/api/v1/{suffix}?run_id={IDS['empty']}").json()
        assert empty["total"] == 0 and empty["run_id"] == str(IDS["empty"])


@pytest.mark.parametrize(
    "path,status",
    [
        ("/api/v1/runs/00000000-0000-4000-8000-000000000000", 404),
        (f"/api/v1/hotspots?run_id={IDS['live']}&limit=201", 422),
        (f"/api/v1/jobs/{IDS['job']}", 401),
        (f"/api/v1/hotspots?run_id={IDS['pending']}", 503),
    ],
)
def test_error_envelope_and_correlation_contract(demo_client, path, status):
    response = demo_client.get(path)
    assert response.status_code == status
    error = ErrorEnvelope.model_validate(response.json()).error
    assert error.request_id == response.headers["X-Request-ID"]
    assert error.retryable == (status == 503)


def test_swagger_demo_is_labeled_prefilled_and_read_only(demo_client):
    assert demo_client.get("/docs").status_code == 200
    document = demo_client.get("/openapi.json").json()
    assert "SYNTHETIC" in document["info"]["title"]
    parameters = document["paths"]["/api/v1/hotspots"]["get"]["parameters"]
    assert next(p for p in parameters if p["name"] == "run_id")["example"] == str(IDS["live"])
    assert demo_client.post("/api/v1/jobs/refresh", json={}).status_code == 403
