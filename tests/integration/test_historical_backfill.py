"""Read-only historical materialization, resumption and compatibility with frozen labels."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import migrate
from firms_factory import ROW, raw_batch
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.models import Region, Run
from firewatch.ml.backfill import merge_backfill_labels, run_backfill, verify_chunk
from firewatch.ml.dataset import load_design, verify_label_package
from firewatch.ml.labels import OutageCatalog
from firewatch.processing.features import FeatureSpec
from firewatch.repositories.ingestion import persist_normalized
from firewatch.schemas.common import Product
from firewatch.sources.firms_normalize import normalize_firms_batch

T = datetime(2022, 5, 1, 6, tzinfo=UTC)
AVAILABLE = datetime(2026, 10, 23, tzinfo=UTC)


@pytest.fixture
def historical(empty_engine, tmp_path):
    migrate(empty_engine)
    design = load_design(Path("configs/target-dataset-v1.yaml"))
    with Session(empty_engine) as session, session.begin():
        area = Region(name=design.region, **design.bbox.model_dump())
        session.add(area)
        session.flush()
        for offset, frp in [(-1, None), (0, "10"), (1, "30"), (2, None)]:
            day = (T + timedelta(days=offset)).date()
            rows = (
                []
                if frp is None
                else [
                    ROW
                    | {
                        "latitude": "39.5",
                        "longitude": "-121.5",
                        "acq_date": day.isoformat(),
                        "acq_time": "0500",
                        "frp": frp,
                        "version": "2.0",
                    }
                ]
            )
            batch = raw_batch(
                tmp_path,
                rows,
                product=Product.NOAA20_SP,
                retrieved=AVAILABLE,
                bbox=design.bbox.model_dump(),
            )
            batch.request = batch.request.model_copy(update={"start_date": day})
            manifest = json.loads(batch.manifest_path.read_bytes())
            manifest["request"] = batch.request.model_dump(mode="json")
            batch.asset.request_metadata = manifest["request"]
            manifest["asset"] = batch.asset.model_dump(mode="json")
            batch.manifest_path.write_text(json.dumps(manifest))
            persist_normalized(session, normalize_firms_batch(batch), area.id)
    settings = Settings(
        database_url="postgresql+psycopg://unused:unused@localhost:1/test",
        admin_api_token="synthetic-only-" + "x" * 40,
        _env_file=None,
    )
    return empty_engine, settings, design


def build(historical, output, **kwargs):
    engine, settings, design = historical
    return run_backfill(
        engine,
        settings,
        output,
        design,
        FeatureSpec(),
        OutageCatalog(),
        start=T,
        end=T + timedelta(hours=6),
        retrieval_cutoff=AVAILABLE,
        **kwargs,
    )


def test_real_builder_creates_eligible_positive_without_db_writes(historical, tmp_path):
    engine, _, _ = historical
    root = tmp_path / "job"
    result = build(historical, root)
    assert result["rows"] == result["eligible_rows"] == 1
    assert result["eligible_labels"] == {"positive": 1}
    assert result["missing_weather_rows"] == 1 and result["weather_requests"] == 1
    assert result["application_database_writes"] == 0
    assert not result["final_test_evaluated"] and not result["model_trained"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(Run)) == 0
    chunk = root / "chunk-0000"
    assert verify_chunk(chunk)["eligible_rows"] == 1
    merged = tmp_path / "merged"
    verified = merge_backfill_labels(root, merged)
    assert verify_label_package(merged, verified["manifest_sha256"]) == verified
    assert verified["rows"] == 1


def test_resume_is_frozen_and_offline_verification_rejects_tampering(
    historical, tmp_path, monkeypatch
):
    import httpx
    from sqlalchemy.engine import Engine

    import firewatch.ml.backfill as module

    root = tmp_path / "job"
    first = build(historical, root)

    def denied(*args, **kwargs):
        raise AssertionError("Resume rebuilt a completed chunk or verification used DB/network")

    monkeypatch.setattr(module, "_prepare", denied)
    assert build(historical, root, resume=True) == first
    with pytest.raises(ValueError, match="exists"):
        build(historical, root)
    engine, settings, design = historical
    with pytest.raises(ValueError, match="configuration changed"):
        run_backfill(
            engine,
            settings,
            root,
            design,
            FeatureSpec(weather_max_age_hours=4),
            OutageCatalog(),
            start=T,
            end=T + timedelta(hours=6),
            retrieval_cutoff=AVAILABLE,
            resume=True,
        )
    monkeypatch.setattr(Engine, "connect", denied)
    monkeypatch.setattr(httpx.Client, "send", denied)
    chunk = root / "chunk-0000"
    assert verify_chunk(chunk)["eligible_rows"] == 1
    proof = json.loads((chunk / "features.json").read_bytes())
    proof["cutoffs"][0]["feature_hashes"] = {}
    (chunk / "features.json").write_text(json.dumps(proof))
    manifest = json.loads((chunk / "manifest.json").read_bytes())
    raw = (chunk / "features.json").read_bytes()
    manifest["files"]["features.json"] = {
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    (chunk / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="features differ"):
        verify_chunk(chunk)


def test_missing_day_is_reported_without_inventing_negative_rows(historical, tmp_path):
    engine, settings, design = historical
    result = run_backfill(
        engine,
        settings,
        tmp_path / "gap",
        design,
        FeatureSpec(),
        OutageCatalog(),
        start=T + timedelta(days=4),
        end=T + timedelta(days=4, hours=6),
        retrieval_cutoff=AVAILABLE,
    )
    assert result["missing_lookback_cutoffs"] == 1
    assert result["rows"] == result["eligible_rows"] == 0
    assert result["labels"] == {}
