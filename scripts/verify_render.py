"""Read-only acceptance checks for the portable historical API, local or deployed."""

import argparse
import json

ML_RUN = "b3f9c88e-d4e2-501b-85b1-8fea26048fb4"


def verify(client) -> dict:
    def get(path, **params):
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    ready = get("/health/ready")
    assert ready["status"] == ready["database"] == "ready"
    assert ready["schema_revision"] == "0003" and ready["artifact_ready"]
    model = get("/api/v1/models/current")
    assert model["scorer_version"] == "forest-independent-2025-v2"
    assert model["scoring_available"] and not model["probability_supported"]
    hotspots = get("/api/v1/hotspots", run_id=ML_RUN)
    summary = get("/api/v1/summary", run_id=ML_RUN)
    assert hotspots["total"] == summary["total"] == 6
    assert hotspots["data_completeness"] == "partial"
    runs = get("/api/v1/runs")["items"]
    alerts = set()
    for run in runs:
        for item in get("/api/v1/hotspots", run_id=run["run_id"])["items"]:
            if item["alert_id"]:
                alerts.add(item["alert_id"])
    for identifier in sorted(alerts):
        evidence = get(f"/api/v1/alerts/{identifier}/verify", verify_artifacts="true")
        assert evidence["local_status"] == "local_match"
        assert evidence["artifact_verification"] == "verified"
    assert len(alerts) == 4
    return {
        "api_ready": True,
        "model_ready": True,
        "historical_hotspots": 6,
        "verified_alerts": len(alerts),
        "data_completeness": "partial",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url", help="Public API URL; otherwise test portable settings in-process"
    )
    args = parser.parse_args()
    if args.base_url:
        import httpx

        with httpx.Client(base_url=args.base_url, timeout=120) as client:
            result = verify(client)
    else:
        from fastapi.testclient import TestClient

        from firewatch.config import Settings
        from firewatch.main import create_app

        settings = Settings(
            model_dir="deploy/render/model", source_artifact_dir="deploy/render/assets"
        )
        with TestClient(create_app(settings)) as client:
            result = verify(client)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
