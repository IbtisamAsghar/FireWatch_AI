"""Export the actual generated OpenAPI document without settings, DB or provider access."""

import argparse
import json
from pathlib import Path
from typing import Any

from firewatch.main import create_app


def field_dictionary(specification: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "api-v1",
        "source": "Generated OpenAPI component schemas",
        "timestamps": "Aware ISO 8601 UTC; unknown values are null",
        "coordinates": "WGS84 decimal degrees; GeoJSON coordinates are [longitude, latitude]",
        "feature_units": {
            "frp_max_mw": "MW",
            "temperature_c": "degC",
            "wind_ms": "m/s",
            "humidity_pct": "%",
            "precipitation_mm": "mm",
        },
        "components": specification["components"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/openapi.json"))
    parser.add_argument("--field-dictionary", type=Path, default=None)
    args = parser.parse_args()
    specification = create_app().openapi()
    operations = [
        operation["operationId"]
        for path in specification["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "delete"}
    ]
    if len(operations) != len(set(operations)):
        raise SystemExit("OpenAPI operation IDs must be unique")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(specification, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.field_dictionary is not None:
        args.field_dictionary.parent.mkdir(parents=True, exist_ok=True)
        args.field_dictionary.write_text(
            json.dumps(field_dictionary(specification), indent=2, sort_keys=True, allow_nan=False)
            + "\n",
            encoding="utf-8",
        )
    print(f"Exported OpenAPI: {len(operations)} operations -> {args.output}")


if __name__ == "__main__":
    main()
