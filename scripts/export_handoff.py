"""Build the frontend handoff from an isolated demo: python -m scripts.export_handoff."""

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from firewatch.config import Settings
from firewatch.db.base import SCHEMA_REVISION
from firewatch.main import create_app
from scripts.demo_api import configured_demo, create_demo_app
from scripts.demo_data import FIXTURE, IDS, LABEL, ROOT, read_requests
from scripts.export_openapi import field_dictionary


def collect_examples(settings: Settings) -> dict[str, Any]:
    app = create_demo_app(settings)
    examples: dict[str, Any] = {}
    with TestClient(app) as client:
        auth = {"Authorization": "Bearer " + settings.admin_api_token.get_secret_value()}
        for operation, path in read_requests().items():
            response = client.get(path, headers=auth if operation == "get_job" else {})
            if response.status_code != 200:
                raise RuntimeError(
                    f"Acceptance example failed: {operation}, HTTP {response.status_code}"
                )
            examples[operation] = {
                "method": "GET",
                "path": path,
                "status": 200,
                "response": response.json(),
            }
        checks = {
            "unknown_run": (f"/api/v1/runs/{'00000000-0000-4000-8000-000000000000'}", 404),
            "invalid_filter": (f"/api/v1/hotspots?run_id={IDS['live']}&limit=201", 422),
            "missing_operator_token": (f"/api/v1/jobs/{IDS['job']}", 401),
            "pending_run": (f"/api/v1/hotspots?run_id={IDS['pending']}", 503),
            "empty_completed_run": (f"/api/v1/hotspots?run_id={IDS['empty']}", 200),
            "replay_hotspots": (f"/api/v1/hotspots?run_id={IDS['replay']}", 200),
        }
        for name, (path, expected) in checks.items():
            response = client.get(path)
            if response.status_code != expected:
                raise RuntimeError(
                    f"Acceptance example failed: {name}, HTTP {response.status_code}"
                )
            examples[name] = {
                "method": "GET",
                "path": path,
                "status": expected,
                "response": response.json(),
            }
    return examples


def export_handoff(output: Path, settings: Settings) -> dict[str, Any]:
    specification = create_app().openapi()
    examples = collect_examples(settings)
    output.mkdir(parents=True, exist_ok=True)
    documents = {
        "openapi.json": specification,
        "examples.json": {"fixture": LABEL, "synthetic": True, "examples": examples},
        "field-dictionary.json": field_dictionary(specification),
        "demo-index.json": {
            "synthetic": True,
            "fixture": LABEL,
            "ids": {key: str(value) for key, value in IDS.items()},
            "requests": read_requests(),
        },
    }
    checksums = {}
    for name, document in documents.items():
        encoded = (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
        (output / name).write_bytes(encoded)
        checksums[name] = hashlib.sha256(encoded).hexdigest()
    for name in (
        "frontend-handoff.md",
        "operator-api.md",
        "demo-walkthrough.md",
        "contracts.md",
        "data-dictionary.md",
        "api-field-dictionary.json",
        "database-schema.md",
        "phase-1.6-checklist.md",
    ):
        source = ROOT / "docs" / name
        destination = output / "docs" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        content = source.read_bytes()
        destination.write_bytes(content)
        checksums["docs/" + name] = hashlib.sha256(content).hexdigest()
    manifest = {
        "schema_version": "handoff-v1",
        "api_version": "api-v1",
        "contracts_version": "contracts-v1",
        "migration_revision": SCHEMA_REVISION,
        "synthetic": True,
        "fixture": LABEL,
        "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
        "read_operations_demonstrated": len(read_requests()),
        "total_operations": 18,
        "files_sha256": checksums,
        "limitations": [
            "Synthetic acceptance fixtures; not real fire observations.",
            "No worker execution, real-data replay, trained model or chain proof is claimed.",
            "Request correlation IDs and regular seeded region UUIDs can vary between exports.",
        ],
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# FireWatch frontend handoff\n\n"
        "openapi.json is the real 18-operation API contract. examples.json contains actual API "
        "responses from isolated synthetic fixtures for every read and representative errors. "
        "field-dictionary.json includes all schemas, nullability, enums and unit notes. "
        "demo-index.json provides UUIDs/paths; manifest.json records versions/checksums.\n\n"
        "No token, database credential, real source manifest or private key is included. "
        "Protected operations need a separately provisioned server-side operator token. "
        "Synthetic scores are not predictions. See repository docs/frontend-handoff.md, "
        "docs/operator-api.md and docs/demo-walkthrough.md.\n",
        encoding="utf-8",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/handoff"))
    args = parser.parse_args()
    manifest = export_handoff(args.output, configured_demo())
    print(
        f"Frontend handoff exported: {manifest['read_operations_demonstrated']} read examples, "
        "18 operations."
    )


if __name__ == "__main__":
    main()
