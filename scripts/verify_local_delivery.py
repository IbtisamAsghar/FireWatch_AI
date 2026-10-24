"""Verify restored historical API/evidence and optional idempotent worker replay."""

import argparse
import json
from pathlib import Path
from uuid import UUID

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from firewatch.config import get_settings
from firewatch.db.models import Alert, Detection, Job
from firewatch.db.session import Database
from firewatch.main import create_app
from firewatch.ml.registry_artifacts import verify_managed_release
from firewatch.schemas.common import JobStatus, JobType
from firewatch.worker import process_one

ROOT = Path(__file__).resolve().parents[1]
ML_RUN = "b3f9c88e-d4e2-501b-85b1-8fea26048fb4"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replay", action="store_true", help="Queue and consume one idempotent replay"
    )
    args = parser.parse_args()
    settings = get_settings()
    receipt = json.loads((ROOT / "docs/local-real-readiness-receipt.json").read_text())
    release = receipt["registry_release"]
    verify_managed_release(
        settings.model_dir / Path(release["artifact_path"]).parent,
        release["managed_release_sha256"],
    )
    database = Database(settings)
    result = {"schema_version": "delivery-repair-readiness-v1", "managed_release_verified": True}
    # In-process TestClient uses a separate transport. Provider transports are
    # explicitly blocked for the entire acceptance run, including worker replay.
    original = httpx.HTTPTransport.handle_request

    def deny_provider(*args, **kwargs):
        raise AssertionError("Delivery acceptance attempted provider HTTP")

    httpx.HTTPTransport.handle_request = deny_provider
    try:
        with TestClient(create_app(settings)) as client:
            assert client.get("/health/ready").status_code == 200
            metadata = client.get("/api/v1/models/current").json()
            assert metadata["scorer_version"] == "forest-independent-2025-v2"
            assert metadata["scoring_available"] and not metadata["probability_supported"]
            result["model_ready"] = True
            result["api_ready"] = True
            responses = {
                name: client.get(f"/api/v1/{name}", params={"run_id": ML_RUN})
                for name in ("hotspots", "hotspots.geojson", "summary")
            }
            assert all(response.status_code == 200 for response in responses.values())
            data = {name: response.json() for name, response in responses.items()}
            assert data["hotspots"]["total"] == data["hotspots.geojson"]["total"] == 6
            assert data["summary"]["total"] == 6
            assert {item["snapshot_id"] for item in data["hotspots"]["items"]} == {
                item["properties"]["snapshot_id"] for item in data["hotspots.geojson"]["features"]
            }
            assert data["hotspots"]["data_completeness"] == "partial"
            result["hotspots"] = 6
            result["completeness"] = "partial"
            with database.session() as session:
                identifiers = list(session.scalars(select(Alert.id)))
                result["observations"] = session.scalar(select(func.count()).select_from(Detection))
            for identifier in identifiers:
                response = client.get(
                    f"/api/v1/alerts/{identifier}/verify", params={"verify_artifacts": True}
                )
                assert response.status_code == 200
                verified = response.json()
                assert verified["local_status"] == "local_match"
                assert verified["artifact_verification"] == "verified"
            result["evidence_records_verified"] = len(identifiers)
            if args.replay:
                path = "/api/v1/jobs/replay"
                body = {"scenario_id": "norcal-ml-weather-v1"}
                assert client.post(path, json=body).status_code == 401
                headers = {
                    "Authorization": "Bearer " + settings.admin_api_token.get_secret_value(),
                    "Idempotency-Key": "delivery-repair-readiness-v1",
                }
                first = client.post(path, json=body, headers=headers)
                assert first.status_code == 202
                second = client.post(path, json=body, headers=headers)
                assert second.status_code == 202
                assert first.json()["job_id"] == second.json()["job_id"]
                assert second.headers["Idempotency-Replayed"] == "true"
                identifier = UUID(first.json()["job_id"])
                with database.session() as session:
                    job = session.get(Job, identifier)
                    pending = job.status == JobStatus.QUEUED
                    if pending:
                        # Never consume unrelated queued jobs while doing acceptance.
                        queued = list(
                            session.scalars(
                                select(Job.id).where(
                                    Job.type.in_([JobType.REFRESH, JobType.REPLAY]),
                                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                                )
                            )
                        )
                        assert queued == [identifier], (
                            "Other work is pending; use its operator workflow"
                        )
                if pending:
                    assert process_one(database.engine, settings)
                progress = client.get(f"/api/v1/jobs/{identifier}", headers=headers)
                assert progress.status_code == 200
                assert progress.json()["status"] == "succeeded"
                assert progress.json()["result_run_id"] == ML_RUN
                result["operator_authentication_verified"] = True
                result["idempotency_verified"] = True
                result["worker_replay_verified"] = True
        output = ROOT / "output/delivery-repair-readiness.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    finally:
        httpx.HTTPTransport.handle_request = original
        database.close()


if __name__ == "__main__":
    raise SystemExit(main())
