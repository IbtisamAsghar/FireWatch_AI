"""Verify real portable scenarios in fresh PostgreSQL schemas with provider HTTP disabled."""

import json
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import yaml
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from firewatch.config import get_settings
from firewatch.db.models import FeatureSetRecord, LiveRunHead, Region
from firewatch.pipeline.replay import replay_scenario
from firewatch.schemas.operator import ReplayConfig


@contextmanager
def isolated_engine(settings):
    url = make_url(settings.database_url.get_secret_value()).set(database="firewatch_test")
    schema = "fw_replay_" + uuid4().hex
    control = create_engine(url, hide_parameters=True)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        url,
        hide_parameters=True,
        connect_args={"options": f"-csearch_path={schema},public -c timezone=UTC"},
    )
    try:
        config = Config("alembic.ini")
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with control.begin() as connection:
            assert schema.startswith("fw_replay_") and len(schema) == 42
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def main() -> int:
    settings = get_settings()
    config = ReplayConfig.model_validate(
        yaml.safe_load(settings.replays_config.read_text(encoding="utf-8"))
    )
    results, comparisons = [], []
    attempts = []
    original = httpx.Client.send

    def denied(*args, **kwargs):
        attempts.append(True)
        raise RuntimeError("Provider HTTP is disabled for replay acceptance")

    httpx.Client.send = denied
    try:
        with tempfile.TemporaryDirectory(prefix="firewatch-replay-portability-") as directory:
            copied = Path(directory) / "replays"
            shutil.copytree(settings.replay_bundle_dir, copied)
            for index, root in enumerate((settings.replay_bundle_dir, copied)):
                local = settings.model_copy(update={"replay_bundle_dir": root})
                with isolated_engine(local) as engine:
                    current = []
                    for scenario in config.scenarios:
                        result = replay_scenario(engine, local, scenario.id)
                        repeat = replay_scenario(engine, local, scenario.id)
                        assert result["run_id"] == repeat["run_id"]
                        assert result["feature_hashes"] == repeat["feature_hashes"]
                        assert result["priority_scores"] == repeat["priority_scores"]
                        assert result["status"] == "completed" and result["mode"] == "replay"
                        with Session(engine) as session:
                            assert session.scalar(select(LiveRunHead.region_id).limit(1)) is None
                            assert not any(session.scalars(select(Region.enabled)))
                            if result["scenario_kind"] == "degraded":
                                features = list(
                                    session.scalars(
                                        select(FeatureSetRecord).where(
                                            FeatureSetRecord.run_id == result["run_id"]
                                        )
                                    )
                                )
                                assert features and all(
                                    f.values["precipitation_24h_mm"] is None for f in features
                                )
                            if result["scenario_kind"] == "quiet":
                                assert result["priority_scores"] == []
                        comparable = {
                            key: result[key]
                            for key in (
                                "run_id",
                                "feature_hashes",
                                "priority_scores",
                                "priorities",
                                "completeness",
                                "coverage_warnings",
                                "alerts",
                                "data_kind",
                                "scenario_kind",
                                "as_of",
                            )
                        }
                        current.append(comparable)
                        if index == 0:
                            results.append(result)
                        print(
                            json.dumps(
                                {
                                    "schema_pass": index + 1,
                                    "scenario_id": scenario.id,
                                    "snapshots": len(result["priority_scores"]),
                                    "status": result["status"],
                                }
                            )
                        )
                    if index == 0:
                        comparisons = current
                    else:
                        assert current == comparisons
    finally:
        httpx.Client.send = original
    assert attempts == []
    output = dict(
        schema_version="phase-3.6-smoke-v1",
        data_kind="real-cached-historical",
        provider_http_disabled=True,
        provider_http_attempts=0,
        fresh_database_passes=2,
        relocated_bundle_pass=True,
        repeats_per_scenario_per_database=2,
        exact_feature_hash_match=True,
        exact_numeric_match=True,
        numeric_tolerance=1e-12,
        latest_live_pointer_unchanged=True,
        regions_remain_disabled=True,
        scenarios=results,
    )
    Path("docs/phase-3.6-smoke.json").write_text(
        json.dumps(output, indent=2) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
