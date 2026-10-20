"""Operator admission acceptance: real PostgreSQL commits, races and rollback."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
import yaml
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_schema import T, alert, batch, outputs, region, running

from firewatch.db.models import AnchorOutbox, Job, Region
from firewatch.repositories.runs import complete_and_publish_run
from firewatch.schemas.common import JobStatus

pytestmark = pytest.mark.integration
TOKEN = "test-only-operator-" + "x" * 40


def prepare(engine):
    with Session(engine) as session:
        area = region(session)
        area.enabled = True
        identifier, name = area.id, area.name
        session.commit()
    return identifier, name


def refresh_body(identifier, **overrides):
    return {
        "region_id": str(identifier),
        "window_start": (T - timedelta(hours=1)).isoformat(),
        "window_end": T.isoformat(),
        **overrides,
    }


def post(client, path, body, key="test-idempotency-key", auth=True, **kwargs):
    headers = {"Idempotency-Key": key}
    if auth:
        headers["Authorization"] = "Bearer " + TOKEN
    return client.post("/api/v1/" + path, json=body, headers=headers, **kwargs)


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def test_refresh_commits_safe_job_and_semantic_retries(api):
    client, engine, settings = api
    area, _ = prepare(engine)
    body = refresh_body(area)
    response = post(client, "jobs/refresh", body)
    assert response.status_code == 202, response.text
    identifier = response.json()["job_id"]
    assert response.headers["Location"] == "/api/v1/jobs/" + identifier
    assert response.headers["Idempotency-Replayed"] == "false"
    assert response.json()["status"] == "queued"
    # Whitespace/order and timezone offsets normalize to the same semantic input.
    retry = post(client, "jobs/refresh", refresh_body(area, window_end="2025-06-10T17:00:00+05:00"))
    assert retry.status_code == 202 and retry.json()["job_id"] == identifier
    assert retry.headers["Idempotency-Replayed"] == "true"
    changed = post(
        client,
        "jobs/refresh",
        refresh_body(area, window_start=(T - timedelta(hours=2)).isoformat()),
    )
    assert changed.status_code == 409
    assert post(client, "jobs/replay", {"scenario_id": "other"}).status_code == 409
    assert count(engine, Job) == 1
    with Session(engine) as session:
        job = session.scalar(select(Job))
        assert job.payload["mode"] == "live"
        assert job.payload["region_id"] == str(area)
        assert job.payload["scorer_version"] == settings.active_scorer
        assert TOKEN not in json.dumps(job.payload)
        session.get(Region, area).enabled = False
        job.status = JobStatus.SUCCEEDED
        session.commit()
    retry = post(client, "jobs/refresh", body)
    assert retry.status_code == 202 and retry.json()["status"] == "succeeded"
    # No handler executes ingestion or changes last completed runs as a side effect.
    assert post(client, "jobs/refresh", body, key="new-disabled-region").status_code == 422


@pytest.mark.parametrize(
    "path,body",
    [
        ("jobs/refresh", {}),
        ("jobs/replay", {}),
        (f"alerts/{uuid4()}/anchor", {}),
    ],
)
def test_every_mutation_requires_auth_before_body_parsing_and_database(api, path, body):
    client, engine, _ = api
    response = post(client, path, body, auth=False)
    assert response.status_code == 401 and response.headers["WWW-Authenticate"] == "Bearer"
    response = client.post(
        "/api/v1/" + path,
        content=b"not-json",
        headers={
            "Authorization": "Bearer wrong",
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 401
    assert count(engine, Job) == 0


@pytest.mark.parametrize(
    "override",
    [
        {"window_start": "2025-06-10T12:00:00Z", "window_end": "2025-06-10T11:00:00Z"},
        {"window_start": "2025-06-01T12:00:00Z"},
        {"window_start": "2025-06-08T10:00:00Z"},
        {"window_end": "2025-06-10T18:00:00Z"},
        {"window_end": "2025-06-10T12:00:00"},
        {"window_end": 1749556800},
        {"bbox": [0, 0, 180, 90]},
        {"scorer_version": "untrusted-model"},
        {"url": "https://untrusted.example"},
    ],
)
def test_refresh_window_and_extra_fields_are_rejected_without_writes(api, override):
    client, engine, _ = api
    identifier, _ = prepare(engine)
    assert post(client, "jobs/refresh", refresh_body(identifier, **override)).status_code == 422
    assert count(engine, Job) == 0


def test_unknown_disabled_oversized_regions_and_request_keys(api):
    client, engine, settings = api
    identifier, _ = prepare(engine)
    assert post(client, "jobs/refresh", refresh_body(uuid4())).status_code == 404
    settings.max_region_area_degrees2 = 1
    assert post(client, "jobs/refresh", refresh_body(identifier)).status_code == 422
    settings.max_region_area_degrees2 = 100
    for key in ("short", "x" * 129, "invalid key"):
        assert post(client, "jobs/refresh", refresh_body(identifier), key=key).status_code == 422
    response = client.post(
        "/api/v1/jobs/refresh",
        json=refresh_body(identifier),
        headers={"Authorization": "Bearer " + TOKEN},
    )
    assert response.status_code == 422
    duplicate = client.post(
        "/api/v1/jobs/refresh",
        json=refresh_body(identifier),
        headers=[
            ("Authorization", "Bearer " + TOKEN),
            ("Idempotency-Key", "first-valid-key"),
            ("Idempotency-Key", "second-valid-key"),
        ],
    )
    assert duplicate.status_code == 422
    assert count(engine, Job) == 0


def test_concurrent_duplicate_admission_and_queue_capacity(api):
    client, engine, settings = api
    identifier, _ = prepare(engine)
    body = refresh_body(identifier)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: post(client, "jobs/refresh", body), range(4)))
    assert {response.status_code for response in responses} == {202}
    assert len({response.json()["job_id"] for response in responses}) == 1
    assert count(engine, Job) == 1
    settings.max_job_queue_depth = 2
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda i: post(client, "jobs/refresh", body, key=f"distinct-request-{i}"), range(4)
            )
        )
    assert sorted(response.status_code for response in responses) == [202, 503, 503, 503]
    assert count(engine, Job) == 2
    # Already-admitted requests survive a full queue.
    assert post(client, "jobs/refresh", body).status_code == 202


def write_scenario(api, assets=None):
    _, engine, settings = api
    with Session(engine) as session:
        area = region(session)
        area.enabled = True
        source = batch(session, area)
        asset_id, name = source.asset_id, area.name
        session.commit()
    config = settings.model_dir / "replays.yaml"
    scenario = {
        "id": "prepared-history",
        "name": "Synthetic acceptance fixture",
        "region_name": name,
        "window_start": (T - timedelta(hours=48)).isoformat(),
        "window_end": T.isoformat(),
        "scorer_version": "baseline-v1",
        "feature_version": "features-v1",
        "asset_ids": assets or [str(asset_id)],
    }
    config.write_text(yaml.safe_dump({"schema_version": "replays-v1", "scenarios": [scenario]}))
    settings.replays_config = config
    return scenario


def test_replay_uses_only_allowlisted_fixed_assets_and_versions(api):
    client, engine, settings = api
    scenario = write_scenario(api)
    assert post(client, "jobs/replay", {"scenario_id": "unknown"}).status_code == 404
    response = post(client, "jobs/replay", {"scenario_id": scenario["id"]})
    assert response.status_code == 202, response.text
    with Session(engine) as session:
        payload = session.scalar(select(Job)).payload
        assert payload["mode"] == "replay" and payload["asset_ids"] == scenario["asset_ids"]
        assert payload["as_of"] == "2025-06-10T12:00:00+00:00"
        assert payload["scorer_version"] == "baseline-v1"
    settings.replays_config.unlink()
    assert post(client, "jobs/replay", {"scenario_id": scenario["id"]}).status_code == 202
    assert (
        post(
            client, "jobs/replay", {"scenario_id": scenario["id"]}, key="new-missing-config"
        ).status_code
        == 503
    )
    assert (
        post(
            client, "jobs/replay", {"scenario_id": "../outside"}, key="new-traversal-key"
        ).status_code
        == 422
    )
    assert (
        post(
            client,
            "jobs/replay",
            {"scenario_id": scenario["id"], "asset_ids": []},
            key="override-asset-key",
        ).status_code
        == 422
    )


def test_replay_missing_assets_or_invalid_trusted_config_rolls_back(api):
    client, engine, settings = api
    scenario = write_scenario(api, [str(uuid4())])
    response = post(client, "jobs/replay", {"scenario_id": scenario["id"]})
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "REPLAY_ASSETS_UNAVAILABLE"
    )
    assert count(engine, Job) == 0
    settings.replays_config.write_text("!!python/object/apply:os.system [echo no]")
    assert post(client, "jobs/replay", {"scenario_id": scenario["id"]}).status_code == 503
    settings.replays_config.write_bytes(b"x" * (256 * 1024 + 1))
    assert post(client, "jobs/replay", {"scenario_id": scenario["id"]}).status_code == 503
    assert count(engine, Job) == 0


def enable_chain(settings):
    # All values are synthetic; no RPC calls are performed by enqueue handlers.
    settings.chain_enabled = True
    settings.chain_id = 31337
    settings.alert_registry_address = "0x" + "a" * 40


def saved_alert(engine):
    with Session(engine) as session:
        run = running(session)
        _, score = outputs(session, run)
        evidence = alert(session, score)
        complete_and_publish_run(session, run.id, T + timedelta(hours=4))
        identifier = evidence.id
        session.commit()
    return identifier


def test_anchor_job_and_outbox_commit_together_and_deduplicate(api):
    client, engine, settings = api
    identifier = saved_alert(engine)
    path = f"alerts/{identifier}/anchor"
    assert post(client, path, {}).status_code == 503
    enable_chain(settings)
    assert post(client, f"alerts/{uuid4()}/anchor", {}).status_code == 404
    assert post(client, path, {"chain_id": 1}).status_code == 422
    response = post(client, path, {})
    assert response.status_code == 202, response.text
    assert count(engine, Job) == count(engine, AnchorOutbox) == 1
    with Session(engine) as session:
        job = session.scalar(select(Job))
        outbox = session.scalar(select(AnchorOutbox))
        assert job.payload["outbox_id"] == str(outbox.id)
        assert job.payload["original_hash"] and outbox.status.value == "queued"
        assert "private_key" not in json.dumps(job.payload)
    assert post(client, path, {}, key="another-anchor-key").status_code == 409
    settings.chain_enabled = False
    assert post(client, path, {}).json()["job_id"] == response.json()["job_id"]
    assert count(engine, Job) == count(engine, AnchorOutbox) == 1


def test_anchor_transaction_failure_never_leaves_orphan_outbox(api):
    client, engine, settings = api
    identifier = saved_alert(engine)
    enable_chain(settings)

    def fail_job(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO jobs"):
            raise RuntimeError("synthetic job insert failure")

    event.listen(engine, "before_cursor_execute", fail_job)
    try:
        assert post(client, f"alerts/{identifier}/anchor", {}).status_code == 500
    finally:
        event.remove(engine, "before_cursor_execute", fail_job)
    assert count(engine, Job) == count(engine, AnchorOutbox) == 0
    assert post(client, f"alerts/{identifier}/anchor", {}).status_code == 202


def test_queue_lock_wait_is_bounded_and_failed_admission_can_retry(api):
    from firewatch.repositories.jobs import ENQUEUE_LOCK

    client, engine, settings = api
    identifier, _ = prepare(engine)
    settings.queue_lock_timeout_ms = 100
    with engine.begin() as connection:
        connection.execute(select(func.pg_advisory_xact_lock(ENQUEUE_LOCK)))
        response = post(client, "jobs/refresh", refresh_body(identifier))
        assert response.status_code == 503
    assert count(engine, Job) == 0
    assert post(client, "jobs/refresh", refresh_body(identifier)).status_code == 202


def test_completed_jobs_release_capacity_but_running_jobs_still_count(api):
    client, engine, settings = api
    identifier, _ = prepare(engine)
    settings.max_job_queue_depth = 1
    body = refresh_body(identifier)
    assert post(client, "jobs/refresh", body).status_code == 202
    with Session(engine) as session:
        session.scalar(select(Job)).status = JobStatus.RUNNING
        session.commit()
    assert post(client, "jobs/refresh", body, key="next-admission-key").status_code == 503
    with Session(engine) as session:
        session.scalar(select(Job)).status = JobStatus.SUCCEEDED
        session.commit()
    assert post(client, "jobs/refresh", body, key="next-admission-key").status_code == 202
    assert count(engine, Job) == 2


def test_legacy_unscoped_anchor_outbox_cannot_schedule_a_second_transaction(api):
    client, engine, settings = api
    identifier = saved_alert(engine)
    enable_chain(settings)
    with Session(engine) as session:
        session.add(AnchorOutbox(alert_id=identifier, network="local"))
        session.commit()
    assert post(client, f"alerts/{identifier}/anchor", {}).status_code == 409
    assert count(engine, Job) == 0 and count(engine, AnchorOutbox) == 1
