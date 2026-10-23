"""Explicitly synthetic evidence inputs, shared by encoding and contract tests."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from feature_factory import inputs, observation

from firewatch.evidence.payload import EvidenceRegion, build_evidence
from firewatch.ml.predict import score_features
from firewatch.processing.features import build_features
from firewatch.repositories.features import EnrichmentPins
from firewatch.repositories.revisions import RevisionPolicy
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.firms_normalize import digest

NOW = datetime(2026, 10, 23, 10, tzinfo=UTC)


def evidence_args(*, baseline=True, missing_weather=False):
    batch, rows, enrichments, satellite = inputs(
        [observation(1, frp=150.0), observation(2, hours=20)], baseline=baseline
    )
    if missing_weather:
        for e in enrichments:
            e.weather.records = []
            e.weather.quality_flags = ["weather_cache_missing"]
            e.weather_assets = []
    features = build_features(batch, rows, enrichments, satellite)
    feature = features[0]
    score = score_features([feature], "baseline-v1", scored_at=NOW)[0]
    pins = EnrichmentPins(
        retrieval_cutoff=NOW,
        weather={feature.lineage["weather"]["cell_key"]: enrichments[0].weather},
        baselines={e.baseline.cell_key: e.baseline for e in enrichments if e.baseline},
        assets=[p for e in enrichments for p in e.weather_assets]
        + [e.baseline_asset for e in enrichments if e.baseline_asset],
        satellite_assets=satellite,
    )
    policy = RevisionPolicy(
        products={r.observation.sensor: r.observation.product for r in rows},
        batch_ids=[p.batch_id for p in satellite],
        retrieval_cutoff=NOW,
    )
    return dict(
        score_id=uuid5(feature.snapshot_id, "synthetic-score"),
        score=score,
        snapshot=batch.snapshots[0].snapshot,
        feature=feature,
        region=EvidenceRegion(
            region_id=uuid5(NAMESPACE_URL, "synthetic-region"),
            name="Synthetic رِیجن",
            bbox=BoundingBox(west=-1, south=-1, east=1, north=1),
        ),
        firms=dict(
            policy=policy.model_dump(mode="json"),
            window_start=(batch.as_of - timedelta(hours=24)).isoformat(),
            selected=[
                {
                    k: r.model_dump(mode="json")[k]
                    for k in ("detection_id", "batch_id", "revision_hash")
                }
                for r in rows
            ],
        ),
        pins=pins,
        run_config_hash=digest({"synthetic": True}),
        feature_stage_hash=digest({"synthetic-feature-stage": True}),
        warnings=["research_only"],
    )


def example(**kwargs):
    return build_evidence(**evidence_args(**kwargs))
