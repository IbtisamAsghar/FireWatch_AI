"""Synthetic empty seasonal inventories exercise fail-closed source coverage checks."""

import hashlib
import json
from datetime import date, timedelta
from uuid import uuid4

import pytest

from firewatch.ml.split_package import load_source_observations


@pytest.fixture
def source(tmp_path):
    datasets = []
    for year in (2022, 2024):
        first, last = date(year, 4, 30), date(year, 11, 1)
        interval = dict(start_date=str(first), end_date=str(last))
        datasets.append(
            dict(
                import_id=str(uuid4()),
                source_manifest=dict(
                    trusted_local_input=True,
                    source_url="https://firms.modaps.eosdis.nasa.gov/download/",
                    attribution_url="https://www.earthdata.nasa.gov/data/tools/firms",
                    product="VIIRS_NOAA20_SP",
                    sensor="NOAA-20",
                    interval=interval,
                    region="northern-california",
                    bbox=dict(west=-122.5, south=38.5, east=-120, north=40.5),
                    sha256="a" * 64,
                    retrieved_at="2026-10-22T00:00:00Z",
                    earliest_feature_cutoff=f"{year}-05-01T00:00:00Z",
                    last_label_cutoff=f"{year}-10-31T18:00:00Z",
                    covered_intervals=[interval],
                ),
                source_batches=[
                    dict(
                        date=str(first + timedelta(days=i)),
                        batch_id=str(uuid4()),
                        coverage_complete=True,
                    )
                    for i in range((last - first).days + 1)
                ],
                quality=dict(revision_count=0, accepted_count=0),
            )
        )
    (tmp_path / "archive-revisions.jsonl").write_bytes(b"")
    return tmp_path, dict(
        datasets=datasets, files_sha256={"archive-revisions.jsonl": hashlib.sha256(b"").hexdigest()}
    )


def save(root, value):
    raw = json.dumps(value).encode()
    (root / "manifest.json").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_complete_empty_source_days_are_accepted(source):
    root, value = source
    assert load_source_observations(root, save(root, value)) == []


@pytest.mark.parametrize(
    "change",
    [
        "missing_day",
        "incomplete",
        "counts",
        "duplicate_import",
        "overlap",
        "coverage",
        "wrong_product",
        "wrong_region",
        "checksum",
        "traversal",
        "missing_file",
        "missing_season",
    ],
)
def test_bad_seasonal_inventory_cannot_create_catalog(source, change):
    root, value = source
    dataset = value["datasets"][0]
    if change == "missing_day":
        dataset["source_batches"].pop()
    elif change == "incomplete":
        dataset["source_batches"][0]["coverage_complete"] = False
    elif change == "counts":
        dataset["quality"]["revision_count"] = 1
    elif change == "duplicate_import":
        value["datasets"][1]["import_id"] = dataset["import_id"]
    elif change == "overlap":
        dataset["source_batches"].append(dataset["source_batches"][0])
    elif change == "coverage":
        dataset["source_manifest"]["covered_intervals"] = []
    elif change == "wrong_product":
        dataset["source_manifest"]["product"] = "VIIRS_NOAA20_NRT"
    elif change == "wrong_region":
        dataset["source_manifest"]["bbox"]["west"] = -123
    elif change == "checksum":
        value["files_sha256"]["archive-revisions.jsonl"] = "0" * 64
    elif change == "traversal":
        value["files_sha256"]["../escape"] = "0" * 64
    elif change == "missing_file":
        value["files_sha256"] = {}
    else:
        value["datasets"].pop()
    with pytest.raises(ValueError):
        load_source_observations(root, save(root, value))


def test_wrong_source_root_checksum_fails(source):
    root, value = source
    save(root, value)
    with pytest.raises(ValueError, match="checksum"):
        load_source_observations(root, "0" * 64)
