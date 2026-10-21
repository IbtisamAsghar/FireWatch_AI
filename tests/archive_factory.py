"""Synthetic local archives: never used as training data or real provider evidence."""

import hashlib

from firms_factory import NOW, raw_batch

from firewatch.sources.archive_manifest import ArchiveManifest


def archive(tmp_path, rows=None, **changes):
    raw = raw_batch(tmp_path, rows)
    data = dict(
        trusted_local_input=True,
        source_url="https://firms.modaps.eosdis.nasa.gov/download/",
        source_kind="archive_portal",
        product=raw.request.product,
        sensor="NOAA-20",
        interval=dict(start_date="2025-06-09", end_date="2025-06-11"),
        region="archive-test",
        bbox=raw.request.bbox,
        sha256=hashlib.sha256(
            raw.manifest_path.with_name("response-02.bin").read_bytes()
        ).hexdigest(),
        retrieved_at=NOW,
        attribution_url="https://www.earthdata.nasa.gov/data/tools/firms",
        earliest_feature_cutoff="2025-06-10T00:00:00Z",
        last_label_cutoff="2025-06-10T23:59:59Z",
        covered_intervals=[dict(start_date="2025-06-09", end_date="2025-06-11")],
    )
    return raw, ArchiveManifest.model_validate(data | changes)
