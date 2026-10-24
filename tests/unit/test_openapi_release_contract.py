"""Freeze the reviewed api-v1 wire contract independently from generated output."""

import json
from pathlib import Path

from firewatch.main import create_app


def test_generated_openapi_matches_reviewed_frontend_contract():
    expected = json.loads(
        (Path(__file__).parents[1] / "fixtures/openapi-v1.json").read_text(encoding="utf-8")
    )
    assert create_app().openapi() == expected, (
        "API contract changed: review frontend compatibility before updating "
        "fixtures/openapi-v1.json"
    )
