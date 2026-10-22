"""Synthetic, provider-shaped acceptance; these fixtures are not real replay evidence."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import yaml
from conftest import isolated_schema, migrate
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_feature_persistence import warm
from test_snapshot_persistence import setup_sources

from firewatch.config import Settings
from firewatch.db.models import Detection, Job, LiveRunHead, Region, Run, SourceAsset, WeatherRecord
from firewatch.pipeline.replay import replay_scenario
from firewatch.pipeline.replay_bundle import export_bundle, import_bundle, load_bundle
from firewatch.processing.features import FeatureSpec
from firewatch.repositories.jobs import replay_payload
from firewatch.repositories.snapshots import build_run_snapshots
from firewatch.schemas.common import RunMode
from firewatch.schemas.operator import ReplayRequest, ReplayScenario

pytestmark = pytest.mark.integration
CLOCK = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture
def bundle_source(empty_engine, tmp_path):
    migrate(empty_engine)
    root = tmp_path / "bundles" / "synthetic-active-v1"
    with Session(empty_engine) as session, session.begin():
        area, run, policy = setup_sources(session, tmp_path)
        run.mode = RunMode.REPLAY
        policy.retrieval_cutoff = CLOCK
        snapshots = build_run_snapshots(session, run.id, policy)
        warm(session, tmp_path, snapshots)
        from firewatch.db.models import IngestionBatch

        assets = list(
            session.scalars(
                select(IngestionBatch.asset_id).where(IngestionBatch.id.in_(policy.batch_ids))
            )
        )
        scenario = ReplayScenario(
            id="synthetic-active-v1",
            name="Synthetic acceptance only",
            region_name=area.name,
            window_start=run.as_of - timedelta(hours=24),
            window_end=run.as_of,
            scorer_version="baseline-v1",
            feature_version="features-v1",
            asset_ids=assets,
        )
        sha = export_bundle(
            session,
            run,
            snapshots,
            policy,
            FeatureSpec(),
            scenario,
            root,
            prepared_at=CLOCK,
            data_kind="synthetic",
            scenario_kind="active",
            coverage_note="Synthetic provider-shaped acceptance fixture; not real observations.",
        )
    config = tmp_path / "replays.yaml"
    configured = scenario.model_copy(update={"bundle_sha256": sha})
    config.write_text(
        yaml.safe_dump(
            dict(schema_version="replays-v1", scenarios=[configured.model_dump(mode="json")])
        ),
        encoding="utf-8",
    )
    settings = Settings(
        database_url="postgresql+psycopg://test:password@localhost:1/firewatch_test",
        admin_api_token="synthetic-test-" + "x" * 40,
        replay_bundle_dir=root.parent,
        replays_config=config,
        raw_data_dir=tmp_path,
        _env_file=None,
    )
    return root, sha, settings


def test_portable_import_and_repeated_replay_use_exact_inputs(bundle_source, tmp_path, monkeypatch):
    root, sha, settings = bundle_source
    monkeypatch.setattr(httpx.Client, "send", lambda *a, **k: pytest.fail("Replay used HTTP"))
    copied = tmp_path / "relocated" / root.name
    shutil.copytree(root, copied)
    config = settings.model_copy(update={"replay_bundle_dir": copied.parent})
    with isolated_schema() as target:
        migrate(target)
        first = replay_scenario(target, config, root.name, now=lambda: CLOCK)
        second = replay_scenario(target, config, root.name, now=lambda: CLOCK + timedelta(hours=1))
        assert first["feature_hashes"] == second["feature_hashes"]
        assert first["priority_scores"] == second["priority_scores"]
        assert first["run_id"] == second["run_id"] and first["mode"] == "replay"
        assert first["data_kind"] == "synthetic" and first["status"] == "completed"
        with Session(target) as session:
            assert session.scalar(select(func.count()).select_from(LiveRunHead)) == 0
            assert not session.scalar(select(Region.enabled))
            assert all(
                Path(p).is_relative_to(copied)
                for p in session.scalars(select(SourceAsset.storage_path))
            )


def test_fresh_database_reruns_match_with_changed_processing_clock(bundle_source):
    root, sha, settings = bundle_source
    results = []
    for offset in (0, 2):
        with isolated_schema() as target:
            migrate(target)
            results.append(
                replay_scenario(
                    target,
                    settings,
                    root.name,
                    now=lambda offset=offset: CLOCK + timedelta(hours=offset),
                )
            )
    assert results[0]["feature_hashes"] == results[1]["feature_hashes"]
    assert results[0]["priority_scores"] == results[1]["priority_scores"]
    assert results[0]["scored_at"] != results[1]["scored_at"]
    assert results[0]["as_of"] == results[1]["as_of"]


def test_later_unrelated_weather_revision_cannot_change_replay(bundle_source):
    root, sha, settings = bundle_source
    bundle = load_bundle(root, sha)
    with isolated_schema() as target:
        migrate(target)
        with Session(target) as session, session.begin():
            import_bundle(session, root, bundle)
            # A newer unrelated revision is deliberately not in the bundle's pins.
            weather = session.scalar(select(WeatherRecord))
            row = {c.name: getattr(weather, c.name) for c in WeatherRecord.__table__.columns}
            row.update(
                id=uuid4(),
                source_version="later-weather-v1",
                temperature_c=99,
                retrieved_at=CLOCK + timedelta(hours=1),
            )
            session.add(WeatherRecord(**row))
        first = replay_scenario(target, settings, root.name, now=lambda: CLOCK + timedelta(hours=2))
        assert first["status"] == "completed"
        run_id = UUID(first["run_id"])
        with Session(target) as session:
            from firewatch.db.models import FeatureSetRecord

            assert (
                session.scalar(
                    select(FeatureSetRecord).where(FeatureSetRecord.run_id == run_id)
                ).values["temperature_c"]
                == 10
            )


@pytest.mark.parametrize("which", ["raw", "canonical", "manifest", "missing"])
def test_tampered_bundle_fails_before_job_or_run(bundle_source, which):
    root, sha, settings = bundle_source
    bundle = load_bundle(root, sha)
    target = (
        root / "manifest.json"
        if which == "manifest"
        else root / "canonical.json"
        if which == "canonical"
        else root / next(f.path for f in bundle.files if f.path.startswith("raw/"))
    )
    if which == "missing":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b"tampered")
    with isolated_schema() as engine:
        migrate(engine)
        with pytest.raises((ValueError, OSError)):
            replay_scenario(engine, settings, root.name, now=lambda: CLOCK)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Job)) == 0
            assert session.scalar(select(func.count()).select_from(Run)) == 0


def test_import_identity_conflict_is_atomic(bundle_source):
    root, sha, _ = bundle_source
    bundle = load_bundle(root, sha)
    with isolated_schema() as target:
        migrate(target)
        with Session(target) as session, session.begin():
            import_bundle(session, root, bundle)
        with Session(target) as session, session.begin():
            row = session.scalar(select(Detection))
            row.frp_mw = 1000
        with (
            Session(target) as session,
            pytest.raises(ValueError, match="conflicts"),
            session.begin(),
        ):
            import_bundle(session, root, bundle)


def test_allowlisted_bundle_can_replay_disabled_region_but_not_enable_live(bundle_source):
    root, sha, settings = bundle_source
    with isolated_schema() as target:
        migrate(target)
        with Session(target) as session, session.begin():
            import_bundle(session, root, load_bundle(root, sha))
        with Session(target) as session:
            payload = replay_payload(session, settings, ReplayRequest(scenario_id=root.name), CLOCK)
            assert payload["bundle_sha256"] == sha and payload["mode"] == "replay"
            assert not session.scalar(select(Region.enabled))


def test_catalog_bundle_mismatch_and_unknown_scenario_fail(bundle_source):
    root, sha, settings = bundle_source
    with isolated_schema() as target:
        migrate(target)
        with pytest.raises(ValueError, match="Unknown"):
            replay_scenario(target, settings, "not-allowlisted", now=lambda: CLOCK)
        catalog = yaml.safe_load(settings.replays_config.read_text())
        catalog["scenarios"][0]["name"] = "Changed scenario"
        settings.replays_config.write_text(yaml.safe_dump(catalog))
        with pytest.raises(ValueError, match="differs"):
            replay_scenario(target, settings, root.name, now=lambda: CLOCK)


def test_replay_cli_completes_and_reports_actual_run(bundle_source, monkeypatch):
    from typer.testing import CliRunner

    import firewatch.cli as cli

    root, _, settings = bundle_source
    with isolated_schema() as target:
        migrate(target)

        class Database:
            engine = target

            def close(self):
                pass

        monkeypatch.setattr(cli, "Database", lambda _: Database())
        monkeypatch.setattr(cli, "get_settings", lambda: settings)
        result = CliRunner().invoke(cli.app, ["replay", "--scenario", root.name])
        assert result.exit_code == 0, result.output
        data = __import__("json").loads(result.stdout)
        assert data["status"] == "completed" and data["mode"] == "replay"
        assert data["run_id"] and data["feature_hashes"]
        missing = CliRunner().invoke(cli.app, ["replay", "--scenario", "unprepared-v1"])
        assert missing.exit_code == 1


def test_bundle_files_changed_after_load_cannot_import(bundle_source):
    root, sha, _ = bundle_source
    bundle = load_bundle(root, sha)
    (root / "canonical.json").write_bytes(b"tampered after verification")
    with isolated_schema() as target:
        migrate(target)
        with (
            Session(target) as session,
            session.begin(),
            pytest.raises(ValueError, match="checksum"),
        ):
            import_bundle(session, root, bundle)
        with Session(target) as session:
            assert session.scalar(select(func.count()).select_from(SourceAsset)) == 0
