"""Recompute the real Phase 4.2 package offline and emit a public count/hash receipt."""

import json
from pathlib import Path

from firewatch.ml.events import EventCatalog, EventReview, reviewed_groups
from firewatch.ml.split_package import verify_split_package
from firewatch.ml.splits import SplitResult

ROOT = Path("data/splits/phase-4.2-final")
MANIFEST_SHA256 = "cca85bb36cc4a207a152b38686f685de961a8b6ebb9c9ddaa807547d17d08264"


def main() -> None:
    verified = verify_split_package(ROOT, MANIFEST_SHA256)
    catalog = EventCatalog.model_validate_json((ROOT / "event-catalog.json").read_bytes())
    review = EventReview.model_validate_json((ROOT / "event-review.json").read_bytes())
    result = SplitResult.model_validate_json((ROOT / "membership.json").read_bytes())
    _, groups = reviewed_groups(catalog, review)
    receipt = dict(
        schema_version="phase-4.2-local-acceptance-v1",
        **verified,
        observations=len(catalog.observations),
        raw_partition_groups=len(catalog.groups),
        reviewed_partition_groups=len(groups),
        near_miss_pairs=len(catalog.near_misses),
        conservative_merge_sets=len(review.merge_sets),
        near_misses_truncated=catalog.near_misses_truncated,
        catalog_sha256=catalog.sha256,
        source_manifest_sha256=catalog.source_manifest_sha256,
        dataset_sha256=result.dataset_sha256,
        split_spec_sha256=result.spec_sha256,
        review_sha256=result.review_sha256,
        event_review_complete=result.event_review_complete,
        partition_counts=result.partition_counts,
        exclusion_counts=result.exclusion_counts,
        final_holdout_usable=result.final_holdout_usable,
        development_cv_ready=result.development_cv_ready,
        release_recommendation=result.release_recommendation,
        review_scope="Local conservative partition review; no independent incident verification",
        scientific_limitations=[
            "Thermal-activity proxies are not verified independent wildfire incidents",
            "All twelve current snapshots are off the frozen six-hour cadence",
            "No usable positive/negative supervised partition or final holdout",
            "Seasonal cadence-aligned feature snapshots and labels are still required",
        ],
        model_fitted=False,
        final_test_evaluated=False,
    )
    Path("docs/phase-4.2-smoke.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
