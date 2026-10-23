"""Public count/hash receipt for the reviewed real seasonal backfill, never a model approval."""

import hashlib
import json
from pathlib import Path

from firewatch.ml.forest_artifact import PortableForest
from firewatch.ml.splits import SplitResult
from firewatch.sources.firms_normalize import digest

ROOT = Path("data/backfills/cadence-seasons-v1")
JOB_SHA = "39876f6a2ae65ceb64e7a1e73ef3f95f0aa1453839aca96c546f8382ad5fb277"
LABELS = Path("data/labels/cadence-seasons-v1")
LABEL_SHA = "0231319527bf79ab05a3ac7b11a98dca5279a3a7a6314b1b36bd51ece1d071dc"
SPLITS = Path("data/splits/cadence-seasons-v1")
TRAINING = Path("data/models/cadence-seasons-v1")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    job = json.loads((ROOT / "job.json").read_bytes())
    summary = json.loads((ROOT / "summary.json").read_bytes())
    if digest(job) != JOB_SHA or sha(LABELS / "manifest.json") != LABEL_SHA:
        raise ValueError("Real backfill identities changed; review a new receipt")
    if summary["processed_cutoffs"] != 1472 or summary["eligible_rows"] != 1819:
        raise ValueError("Real backfill counts changed")
    chunks = {row["path"]: row["manifest_sha256"] for row in summary["chunks"]}
    if len(chunks) != 92 or any(
        sha(ROOT / name / "manifest.json") != pin for name, pin in chunks.items()
    ):
        raise ValueError("Real backfill chunk identity changed")
    split = SplitResult.model_validate_json((SPLITS / "membership.json").read_bytes())
    label_manifest = json.loads((LABELS / "manifest.json").read_bytes())
    split_manifest = json.loads((SPLITS / "manifest.json").read_bytes())
    if (
        label_manifest["backfill_source_sha256"] != JOB_SHA
        or split_manifest["label_package_sha256"] != LABEL_SHA
    ):
        raise ValueError("Backfill label/split provenance changed")
    model = None
    training_sha = None
    calibration = None
    if TRAINING.exists():
        manifest = json.loads((TRAINING / "manifest.json").read_bytes())
        for name in ("model.json", "training-report.json"):
            if sha(TRAINING / name) != manifest["files"][name]["sha256"]:
                raise ValueError("Training model/report checksum mismatch")
        model = PortableForest.model_validate_json((TRAINING / "model.json").read_bytes())
        training_sha = sha(TRAINING / "manifest.json")
        calibration = json.loads((TRAINING / "training-report.json").read_bytes())["calibration"][
            "status"
        ]
    if Path("data/evaluations/final-test-ledger.sqlite3").exists():
        raise ValueError("This local backfill receipt requires the final test to remain sealed")
    receipt = dict(
        schema_version="historical-training-backfill-acceptance-v1",
        backfill_job_sha256=JOB_SHA,
        backfill_collection_sha256=digest(chunks),
        label_manifest_sha256=LABEL_SHA,
        split_manifest_sha256=sha(SPLITS / "manifest.json"),
        training_manifest_sha256=training_sha,
        scheduled_cutoffs=1472,
        completed_chunks=92,
        snapshots=1975,
        eligible_rows=1819,
        eligible_labels=summary["eligible_labels"],
        unknown_rows=156,
        missing_lookback_cutoffs=summary["missing_lookback_cutoffs"],
        missing_temperature_rows=summary["missing_weather_rows"],
        split_strategy=split.strategy,
        training_ready=split.training_ready,
        final_holdout_usable=split.final_holdout_usable,
        partition_counts=split.partition_counts,
        candidate_fitted=model is not None,
        candidate_probability_supported=model.probability_supported if model else False,
        calibration_status=calibration,
        final_test_status="sealed",
        final_test_evaluated=False,
        model_approved=False,
        active_scorer_changed=False,
        feature_backfill_database_writes=0,
        weather_plan_requests=288,
        successful_bounded_weather_requests=1,
        limitations=[
            "Most snapshots lack weather; missing flags and training-only imputation remain",
            "The sparse calibration cohort requires forward temporal CV and uncalibrated scores",
            "Reviewed thermal event groups are partition proxies, not verified fire incidents",
            "No final held-out model performance or independent ML release approval is claimed",
            "Weather fetched after the frozen job cutoff requires a new backfill job",
        ],
    )
    Path("docs/training-backfill-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
