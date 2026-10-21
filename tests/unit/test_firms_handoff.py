"""Cross-path acceptance and fail-closed handoff verification with invented source rows."""

import json
import sqlite3
from pathlib import Path

import pytest
from archive_factory import archive
from firms_factory import ROW

from firewatch.sources.archive_stage import atomic_json, prepare_archive
from firewatch.sources.firms_handoff import HandoffError, export_firms_handoff, scan_secrets
from firewatch.sources.firms_normalize import normalize_firms_batch


def completed(tmp_path, rows=None):
    raw, manifest = archive(tmp_path, rows)
    prepared = prepare_archive(Path(raw.asset.storage_path), manifest, tmp_path / "prepared")
    days = ["2025-06-09", "2025-06-10", "2025-06-11"]
    from uuid import uuid4

    path = prepared.folder / "handoff.json"
    atomic_json(
        path,
        {
            "schema_version": "firms-archive-import-v1",
            "status": "completed",
            "import_id": str(uuid4()),
            "source_manifest": manifest.model_dump(mode="json"),
            "original_path": str(prepared.folder / "original.csv"),
            "quality_path": str(prepared.folder / "quality.json"),
            "inventory": prepared.quality,
            "source_batches": [
                dict(date=day, batch_id=str(uuid4()), coverage_complete=True) for day in days
            ],
        },
    )
    document = json.loads(path.read_text())
    document["source_batch_ids"] = [item["batch_id"] for item in document["source_batches"]]
    atomic_json(path, document)
    return raw, prepared, path


def test_export_preserves_every_corrected_revision_and_shared_schema(tmp_path):
    rows = [ROW | {"acq_time": "37"}, ROW | {"frp": "18"}, dict(ROW)]
    raw, prepared, path = completed(tmp_path, rows)
    report = export_firms_handoff([path], [], tmp_path / "handoff")
    saved = [
        json.loads(line)["revision"]
        for line in (tmp_path / "handoff/archive-revisions.jsonl").read_text().splitlines()
    ]
    live = normalize_firms_batch(raw)
    assert {r["revision_hash"] for r in saved} == {r.revision_hash for r in live.revisions}
    assert report["archive_totals"] == dict(
        fetched=3, accepted=1, rejected=0, duplicates=2, revisions=3
    )
    assert not report["evaluation"]["ready_for_event_separated_evaluation"]
    assert report["evaluation"]["independent_event_count"] is None
    assert report["files_sha256"]["archive-revisions.jsonl"]
    assert (tmp_path / "handoff/manifest.json").is_file()


@pytest.mark.parametrize("corruption", ["original", "revision", "quality", "status", "duplicate"])
def test_corruption_never_publishes_completed_manifest(tmp_path, corruption):
    _, prepared, path = completed(tmp_path)
    paths = [path]
    if corruption == "original":
        (prepared.folder / "original.csv").write_text("changed")
    elif corruption == "revision":
        with sqlite3.connect(prepared.index_path) as db:
            db.execute("UPDATE revisions SET hash=?", ("0" * 64,))
    elif corruption in ("quality", "status"):
        handoff = json.loads(path.read_text())
        if corruption == "quality":
            handoff["inventory"]["accepted_count"] += 1
        else:
            handoff["status"] = "running"
        atomic_json(path, handoff)
    else:
        paths *= 2
    with pytest.raises(HandoffError):
        export_firms_handoff(paths, [], tmp_path / "out")
    assert not (tmp_path / "out/manifest.json").exists()


def test_secret_scan_catches_chunk_boundary_without_echoing_secret(tmp_path):
    secret = "invented-sensitive-value"
    path = tmp_path / "fixture.bin"
    path.write_bytes(b"x" * (1024 * 1024 - 3) + secret.encode())
    with pytest.raises(HandoffError) as error:
        scan_secrets([path], [secret])
    assert secret not in str(error.value)


def test_export_rejects_provider_secret_before_publication(tmp_path):
    secret = "invented-sensitive-value"
    _, _, path = completed(tmp_path, [ROW | {"confidence": secret}])
    with pytest.raises(HandoffError, match="Secret material"):
        export_firms_handoff([path], [], tmp_path / "out", secrets=[secret])
    assert not (tmp_path / "out/manifest.json").exists()


def test_valid_empty_archive_exports_zero_without_inventing_labels(tmp_path):
    _, _, path = completed(tmp_path, [])
    report = export_firms_handoff([path], [], tmp_path / "out")
    assert report["archive_totals"]["revisions"] == 0
    assert not report["evaluation"]["ready_for_supervised_training"]


def test_output_cannot_overwrite_existing_handoff(tmp_path):
    _, _, path = completed(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(HandoffError, match="new directory"):
        export_firms_handoff([path], [], output)


def refresh_document(tmp_path):
    from uuid import uuid4

    from firms_factory import NOW, raw_batch

    from firewatch.repositories.revisions import SelectedDetection
    from firewatch.schemas.firms_refresh import (
        RefreshChunk,
        RefreshCounts,
        RefreshSpec,
        RefreshState,
        summarize_refresh,
    )
    from firewatch.sources.archive_stage import hash_file

    raw = raw_batch(tmp_path)
    revision = normalize_firms_batch(raw).revisions[0]
    batch_id = uuid4()
    spec = RefreshSpec(region="archive-test", as_of=NOW, products=[raw.request.product])
    state = RefreshState(
        refresh_id=uuid4(),
        region_id=uuid4(),
        bbox=raw.request.bbox,
        spec=spec,
        updated_at=NOW,
        chunks=[
            RefreshChunk(
                key="one",
                request=raw.request,
                status="succeeded",
                batch_id=batch_id,
                counts=RefreshCounts(fetched=1, accepted=1, rejected=0, duplicate=0),
                completed_at=NOW,
            )
        ],
    )
    records = tmp_path / "live.jsonl"
    records.write_text(
        SelectedDetection(
            detection_id=uuid4(),
            batch_id=batch_id,
            revision_hash=revision.revision_hash,
            physical_key=revision.physical_key,
            observation=revision.observation,
        ).model_dump_json()
        + "\n"
    )
    path = tmp_path / "refresh.json"
    document = dict(
        schema_version="firms-refresh-handoff-v1",
        state=state.model_dump(mode="json"),
        summary=summarize_refresh(state).model_dump(mode="json"),
        normalized_records_path=str(records),
        normalized_records_sha256=hash_file(records),
        normalized_records_count=1,
        source_batch_ids=[str(batch_id)],
    )
    atomic_json(path, document)
    return path, document


def test_live_handoff_uses_same_units_and_exports_verified_records(tmp_path):
    _, _, archive_path = completed(tmp_path)
    refresh_path, document = refresh_document(tmp_path)
    report = export_firms_handoff([archive_path], [refresh_path], tmp_path / "out")
    live = report["refreshes"][0]
    assert live["normalized_records_count"] == 1
    target = tmp_path / "out" / live["normalized_records_file"]
    assert target.read_bytes() == Path(document["normalized_records_path"]).read_bytes()
    assert live["normalized_records_file"] in report["files_sha256"]


@pytest.mark.parametrize(
    "field",
    ["normalized_records_sha256", "normalized_records_count", "summary", "source_batch_ids"],
)
def test_live_corruption_is_blocked(tmp_path, field):
    _, _, archive_path = completed(tmp_path)
    refresh_path, document = refresh_document(tmp_path)
    if field == "summary":
        document[field]["coverage"] = "unavailable"
    elif field == "source_batch_ids":
        document[field] = []
    else:
        document[field] = "0" * 64 if field.endswith("sha256") else 2
    atomic_json(refresh_path, document)
    with pytest.raises(HandoffError):
        export_firms_handoff([archive_path], [refresh_path], tmp_path / "out")
    assert not (tmp_path / "out/manifest.json").exists()


@pytest.mark.parametrize(
    "product", ["VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "VIIRS_NOAA20_SP", "VIIRS_NOAA21_SP"]
)
def test_all_products_share_exact_archive_and_live_revision_schema(tmp_path, product):
    from firms_factory import raw_batch

    from firewatch.schemas.common import Product
    from firewatch.sources.archive_manifest import ArchiveManifest

    _, template = archive(tmp_path)
    rows = [
        ROW
        | {
            "satellite": "N21" if "NOAA21" in product else "N20",
            "version": "2.0NRT" if product.endswith("NRT") else "2.0",
            "acq_time": "37",
        }
    ]
    raw = raw_batch(tmp_path, rows, product=Product(product))
    manifest = ArchiveManifest.model_validate(
        template.model_dump()
        | {
            "product": product,
            "sensor": "NOAA-21" if "NOAA21" in product else "NOAA-20",
            "sha256": raw.asset.content_hash,
        }
    )
    prepared = prepare_archive(Path(raw.asset.storage_path), manifest, tmp_path / "cross-path")
    live = normalize_firms_batch(raw).revisions[0]
    with sqlite3.connect(prepared.index_path) as db:
        saved = db.execute("SELECT hash,observation FROM revisions").fetchone()
    assert saved[0] == live.revision_hash
    assert json.loads(saved[1]) == live.observation.model_dump(mode="json")
    assert live.observation.acquired_at.hour == 0
    assert live.observation.acquired_at.minute == 37


def test_missing_empty_day_provenance_cannot_claim_complete_archive(tmp_path):
    _, _, path = completed(tmp_path)
    document = json.loads(path.read_text())
    document["source_batches"].pop(0)
    atomic_json(path, document)
    with pytest.raises(HandoffError, match="daily source batch"):
        export_firms_handoff([path], [], tmp_path / "out")
