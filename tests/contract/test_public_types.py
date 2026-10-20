"""Frontend-facing timestamp, nullability, unit metadata and coordinate contracts."""

import pytest
from pydantic import ValidationError

from firewatch.config import Settings
from firewatch.main import create_app
from firewatch.schemas.api import Geometry
from firewatch.schemas.operator import RefreshRequest
from scripts.demo_api import create_demo_app, demo_schema


@pytest.mark.parametrize("value", ["2025-06-10T12:00:00", 1749556800, "not-a-time"])
def test_operator_timestamp_contract_rejects_naive_epochs_and_invalid_text(value):
    with pytest.raises(ValidationError):
        RefreshRequest(
            region_id="00000000-0000-4000-8000-000000000000",
            window_start="2025-06-10T11:00:00Z",
            window_end=value,
        )


def test_aware_timestamps_normalize_and_public_schema_describes_units_and_nulls():
    body = RefreshRequest(
        region_id="00000000-0000-4000-8000-000000000000",
        window_start="2025-06-10T16:00:00+05:00",
        window_end="2025-06-10T17:00:00+05:00",
    )
    assert body.model_dump(mode="json")["window_end"] == "2025-06-10T12:00:00Z"
    components = create_app().openapi()["components"]["schemas"]
    fields = components["HotspotView"]["properties"]
    assert "MW" in fields["frp_max_mw"]["description"]
    assert {item["type"] for item in fields["escalation_probability"]["anyOf"]} == {
        "number",
        "null",
    }
    assert (
        "longitude, latitude" in components["Geometry"]["properties"]["coordinates"]["description"]
    )


def test_geojson_coordinate_bounds_are_part_of_public_dto():
    assert Geometry(coordinates=(180, 90)).model_dump(mode="json")["coordinates"] == [180, 90]
    with pytest.raises(ValidationError):
        Geometry(coordinates=(71, 91))


def test_demo_rejects_production_and_normal_databases_before_connecting():
    configured = Settings(
        database_url="postgresql+psycopg://test:private-pass@localhost:1/firewatch",
        admin_api_token="contract-test-operator-" + "x" * 40,
        _env_file=None,
    )
    with pytest.raises(ValueError, match="_test"):
        with demo_schema(configured):
            pytest.fail("Normal database must never be used for synthetic demo writes")
    with pytest.raises(ValueError, match="production"):
        create_demo_app(configured.model_copy(update={"app_env": "production"}))


def test_committed_field_dictionary_matches_generated_public_contract():
    import json

    from scripts.demo_data import ROOT
    from scripts.export_openapi import field_dictionary

    committed = json.loads((ROOT / "docs/api-field-dictionary.json").read_text(encoding="utf-8"))
    assert committed == field_dictionary(create_app().openapi())
