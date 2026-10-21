import json
import sqlite3
from pathlib import Path

import pytest
from archive_factory import archive
from firms_factory import ROW
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.sources.archive_manifest import ArchiveError, ArchiveManifest
from firewatch.sources.archive_stage import prepare_archive
from firewatch.sources.firms_normalize import normalize_firms_batch


def prepared(tmp_path, rows=None, **changes):
    raw, manifest = archive(tmp_path, rows, **changes)
    return raw, prepare_archive(
        Path(raw.asset.storage_path), manifest, tmp_path / "out", chunk_rows=1
    )


def test_archive_reuses_exact_live_normalization(tmp_path):
    raw, result = prepared(tmp_path)
    live = normalize_firms_batch(raw).revisions[0]
    with sqlite3.connect(result.index_path) as db:
        saved = db.execute("SELECT hash,observation FROM revisions").fetchone()
    assert saved[0] == live.revision_hash
    assert json.loads(saved[1]) == live.observation.model_dump(mode="json")
    assert (result.folder / "original.csv").read_bytes() == Path(
        raw.asset.storage_path
    ).read_bytes()
    assert result.quality["training_classes"] == dict(unlabeled=1, positive=None, negative=None)


def test_duplicate_revisions_counts_and_provenance(tmp_path):
    _, result = prepared(tmp_path, [dict(ROW)] * 25 + [ROW | {"frp": "18"}])
    assert result.quality["accepted_count"] == 1
    assert result.quality["duplicate_count"] == 25
    assert result.quality["revised_count"] == 1
    with sqlite3.connect(result.index_path) as db:
        assert db.execute("SELECT count(*) FROM row_provenance").fetchone()[0] == 26
        assert db.execute("SELECT max(occurrences) FROM revisions").fetchone()[0] == 25
        samples = db.execute("SELECT row_numbers FROM revisions WHERE occurrences=25").fetchone()[0]
        assert len(json.loads(samples)) == 20


def test_missing_coverage_is_distinct_from_zero_detection_days(tmp_path):
    _, result = prepared(
        tmp_path,
        covered_intervals=[dict(start_date="2025-06-10", end_date="2025-06-10")],
        expected_active_intervals=[dict(start_date="2025-06-11", end_date="2025-06-11")],
    )
    assert result.quality["missing_date_intervals"] == [
        dict(start_date="2025-06-09", end_date="2025-06-09"),
        dict(start_date="2025-06-11", end_date="2025-06-11"),
    ]
    assert result.quality["unexpectedly_empty_intervals"] == [
        dict(start_date="2025-06-11", end_date="2025-06-11")
    ]
    assert not result.quality["observation_history_complete"]


def test_empty_archive_and_rejected_rows_are_honest(tmp_path):
    _, empty = prepared(tmp_path, [])
    assert empty.quality["accepted_count"] == 0
    assert not empty.quality["ready_for_supervised_training"]
    assert len(empty.quality["empty_intervals"]) == 1
    _, bad = prepared(tmp_path, [ROW | {"frp": "-1"}, ROW | {"acq_date": "bad"}])
    assert bad.quality["rejected_count"] == 2
    assert len((bad.folder / "rejected.jsonl").read_text().splitlines()) == 2
    assert not bad.quality["observation_history_complete"]


@pytest.mark.parametrize("changes", [{"satellite": "N21"}, {"version": "2.0"}])
def test_mixed_products_stop_before_database(tmp_path, changes):
    with pytest.raises(ArchiveError, match="Mixed"):
        prepared(tmp_path, [dict(ROW), ROW | changes])


@pytest.mark.parametrize(
    "changes",
    [
        {"earliest_feature_cutoff": "2025-06-09T12:00:00Z"},
        {"last_label_cutoff": "2025-06-11T12:00:00Z"},
        {"sensor": "NOAA-21"},
        {"source_url": "https://firms.modaps.eosdis.nasa.gov/download/?token=secret"},
        {"source_url": "https://user:secret@firms.modaps.eosdis.nasa.gov/download/"},
        {"source_url": "https://firms.modaps.eosdis.nasa.gov/api/area/csv/secret/key"},
        {"interval": {"start_date": 0, "end_date": "2025-06-11"}},
    ],
)
def test_provenance_and_24_hour_margins_required(tmp_path, changes):
    with pytest.raises(ValueError):
        archive(tmp_path, **changes)


def test_size_rows_checksum_and_malformed_csv_bounds(tmp_path):
    raw, manifest = archive(tmp_path)
    path = Path(raw.asset.storage_path)
    with pytest.raises(ArchiveError):
        prepare_archive(path, manifest, tmp_path / "a", max_bytes=1)
    with pytest.raises(ArchiveError):
        prepare_archive(path, manifest, tmp_path / "b", max_rows=1, chunk_rows=0)
    changed = manifest.model_copy(update={"sha256": "0" * 64})
    with pytest.raises(ArchiveError, match="checksum"):
        prepare_archive(path, changed, tmp_path / "c")
    import hashlib

    path.write_bytes(b"latitude,longitude\n1,2\n")
    changed = ArchiveManifest.model_validate(
        manifest.model_dump() | {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    )
    with pytest.raises(ArchiveError, match="columns"):
        prepare_archive(path, changed, tmp_path / "d")


def test_dry_run_cli_does_not_load_database_settings(tmp_path, monkeypatch):
    raw, manifest = archive(tmp_path)
    file = tmp_path / "archive.json"
    file.write_text(manifest.model_dump_json())
    monkeypatch.setattr(
        "firewatch.cli.get_settings", lambda: pytest.fail("Dry run loaded settings")
    )
    result = CliRunner().invoke(
        app,
        [
            "import-firms",
            "--path",
            raw.asset.storage_path,
            "--manifest",
            str(file),
            "--output",
            str(tmp_path / "out"),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not json.loads(result.output)["database_modified"]


def test_confirmed_empty_archive_is_complete_observation_history(tmp_path):
    _, result = prepared(
        tmp_path,
        [],
        confirmed_empty_intervals=[dict(start_date="2025-06-09", end_date="2025-06-11")],
    )
    assert result.quality["observation_history_complete"]
    assert not result.quality["unexpectedly_empty_intervals"]
    assert not result.quality["ready_for_supervised_training"]
    assert result.quality["inventory"][0]["observations"] == 0


def test_streaming_row_limit_and_correction_variant_bound(tmp_path):
    raw, manifest = archive(tmp_path, [dict(ROW), dict(ROW)])
    with pytest.raises(ArchiveError, match="row bound"):
        prepare_archive(Path(raw.asset.storage_path), manifest, tmp_path / "limited", max_rows=1)
    raw, manifest = archive(tmp_path, [ROW | {"frp": str(n)} for n in range(65)])
    with pytest.raises(ArchiveError, match="correction variants"):
        prepare_archive(Path(raw.asset.storage_path), manifest, tmp_path / "variants")


def test_delivered_archive_brightness_aliases_preserve_raw_schema(tmp_path):
    import hashlib

    from firewatch.sources.firms import parse_firms_csv

    raw, manifest = archive(tmp_path)
    live = normalize_firms_batch(raw).revisions[0].observation
    path = Path(raw.asset.storage_path)
    content = (
        path.read_bytes()
        .replace(b"bright_ti4", b"brightness")
        .replace(b"bright_ti5", b"bright_t31")
    )
    path.write_bytes(content)
    manifest = ArchiveManifest.model_validate(
        manifest.model_dump() | {"sha256": hashlib.sha256(content).hexdigest()}
    )
    columns, rows = parse_firms_csv(content)
    assert "brightness" in columns and "bright_t31" in columns
    assert "bright_ti4" not in rows[0]
    result = prepare_archive(path, manifest, tmp_path / "aliases")
    with sqlite3.connect(result.index_path) as db:
        saved = json.loads(db.execute("SELECT observation FROM revisions").fetchone()[0])
    assert saved["dedup_key"] == live.dedup_key
    assert saved["brightness_k"] == live.brightness_k
    assert saved["raw_values"]["normalization"]["bright_ti5_k"] == 290.1
    assert saved["raw_values"]["provider_row"]["brightness"] == "330.2"


def test_public_zip_reference_matches_manifest_sensor_and_request(tmp_path):
    _, manifest = archive(
        tmp_path,
        archive_request_id="828115",
        source_url="https://firms.modaps.eosdis.nasa.gov/data/download/DL_FIRE_J1V-C2_828115.zip",
    )
    assert manifest.archive_request_id == "828115"
    with pytest.raises(ValueError):
        ArchiveManifest.model_validate(manifest.model_dump() | {"archive_request_id": "828116"})
    with pytest.raises(ValueError):
        ArchiveManifest.model_validate(
            manifest.model_dump()
            | {
                "source_url": "https://firms.modaps.eosdis.nasa.gov/data/download/DL_FIRE_J2V-C2_828115.zip"
            }
        )


def test_schema_export_and_real_manifests_are_valid():
    root = Path(__file__).resolve().parents[2]
    assert (
        json.loads((root / "docs/firms-archive-manifest.schema.json").read_text(encoding="utf-8"))
        == ArchiveManifest.model_json_schema()
    )
    for path in (root / "docs/manifests").glob("*-archive.json"):
        ArchiveManifest.model_validate_json(path.read_text(encoding="utf-8"))
