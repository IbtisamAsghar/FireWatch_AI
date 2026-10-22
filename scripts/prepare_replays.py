"""Prepare the three real cached replay bundles declared in configs/replay-preparation.yaml."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import yaml
from sqlalchemy import select

from firewatch.config import get_settings
from firewatch.db.models import Region, Run
from firewatch.db.session import Database
from firewatch.pipeline.replay_bundle import export_bundle
from firewatch.processing.features import load_feature_spec
from firewatch.repositories.snapshots import build_run_snapshots, policy_for_snapshots
from firewatch.schemas.common import Product, RunMode, Sensor
from firewatch.schemas.operator import ReplayScenario
from firewatch.sources.firms_normalize import digest


def main() -> int:
    settings = get_settings()
    configuration = yaml.safe_load(
        Path("configs/replay-preparation.yaml").read_text(encoding="utf-8")
    )
    if configuration["schema_version"] != "replay-preparation-v1":
        raise ValueError("Unknown preparation specification")
    clock = datetime.now(UTC)
    features = load_feature_spec(settings.features_config)
    database = Database(settings)
    scenarios, catalog = [], []
    try:
        for definition in configuration["scenarios"]:
            cutoff = datetime.fromisoformat(definition["as_of"].replace("Z", "+00:00"))
            with database.session() as session:
                area = session.scalar(select(Region).where(Region.name == configuration["region"]))
                if area is None:
                    raise ValueError("Import real historical data before replay preparation")
                policy = policy_for_snapshots(
                    session, area.id, cutoff, {Sensor.NOAA20: Product.NOAA20_SP}, clock
                )
                run = Run(
                    id=uuid5(NAMESPACE_URL, "prepare:" + definition["id"]),
                    region_id=area.id,
                    mode=RunMode.REPLAY,
                    as_of=cutoff,
                    status="running",
                    started_at=clock,
                    config_hash=digest(features.model_dump(mode="json")),
                    run_key="prepare:" + definition["id"],
                    scorer_version="baseline-v1",
                    feature_version="features-v1",
                    completeness="unavailable",
                    stage_status={},
                )
                session.add(run)
                session.flush()
                snapshots = build_run_snapshots(session, run.id, policy)
                from firewatch.db.models import IngestionBatch

                assets = sorted(
                    session.scalars(
                        select(IngestionBatch.asset_id).where(
                            IngestionBatch.id.in_(policy.batch_ids)
                        )
                    ),
                    key=str,
                )
                scenario = ReplayScenario(
                    id=definition["id"],
                    name=definition["name"],
                    region_name=area.name,
                    window_start=cutoff - timedelta(hours=24),
                    window_end=cutoff,
                    scorer_version="baseline-v1",
                    feature_version="features-v1",
                    asset_ids=assets,
                )
                output = settings.replay_bundle_dir / scenario.id
                bundle_hash = export_bundle(
                    session,
                    run,
                    snapshots,
                    policy,
                    features,
                    scenario,
                    output,
                    prepared_at=clock,
                    data_kind="real",
                    scenario_kind=definition["kind"],
                    coverage_note=definition["coverage_note"],
                    secrets=[
                        settings.admin_api_token.get_secret_value(),
                        settings.firms_map_key.get_secret_value() if settings.firms_map_key else "",
                    ],
                )
                if definition["kind"] == "quiet" and snapshots.candidate_count != 0:
                    raise ValueError(
                        "Quiet scenario must be verified empty in its candidate window"
                    )
                scenarios.append(
                    scenario.model_copy(update={"bundle_sha256": bundle_hash}).model_dump(
                        mode="json"
                    )
                )
                catalog.append(
                    dict(
                        scenario_id=scenario.id,
                        data_kind="real",
                        scenario_kind=definition["kind"],
                        as_of=cutoff.isoformat(),
                        coverage_note=definition["coverage_note"],
                        bundle_sha256=bundle_hash,
                        bundle_directory="data/replays/" + scenario.id,
                        candidate_observations=snapshots.candidate_count,
                        history_observations=snapshots.history_count,
                        snapshots=len(snapshots.snapshots),
                        satellite_asset_ids=[str(i) for i in assets],
                    )
                )
                # The preparation Run and snapshots roll back; only bundle files are produced.
                print(json.dumps(catalog[-1]))
        settings.replays_config.write_text(
            yaml.safe_dump(dict(schema_version="replays-v1", scenarios=scenarios), sort_keys=False),
            encoding="utf-8",
        )
        Path("docs/replay-catalog.json").write_text(
            json.dumps(
                dict(
                    schema_version="replay-catalog-v1",
                    data_kind="real",
                    scenarios=catalog,
                    prepared_at=clock.isoformat(),
                    raw_bundles_committed=False,
                    source_credit=[
                        "NASA FIRMS NOAA-20 VIIRS standard processing",
                        "Open-Meteo GFS historical forecasts",
                        "NASA POWER 2010-2019",
                    ],
                ),
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
