"""Export the actual generated OpenAPI document without settings, DB or provider access."""

import argparse
import json
from pathlib import Path

from firewatch.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/openapi.json"))
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
    print(f"Exported OpenAPI: {len(operations)} operations -> {args.output}")


if __name__ == "__main__":
    main()
