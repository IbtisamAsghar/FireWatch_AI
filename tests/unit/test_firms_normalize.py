"""FIRMS normalization acceptance using provider-shaped synthetic rows."""

import json
from datetime import UTC, datetime

import pytest
from firms_factory import NOW, ROW, raw_batch
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.schemas.common import Product
from firewatch.sources.firms_normalize import (
    NormalizationPolicy,
    load_saved_firms_batch,
    normalize_firms_batch,
    write_quality_reports,
)


def normalized(tmp_path, **changes):
    return normalize_firms_batch(raw_batch(tmp_path, [ROW | changes]))


@pytest.mark.parametrize(
    "clock, hour, minute", [("0037", 0, 37), ("37", 0, 37), ("0", 0, 0), ("2359", 23, 59)]
)
def test_utc_and_zero_padding(tmp_path, clock, hour, minute):
    obs = normalized(tmp_path, acq_time=clock).revisions[0].observation
    assert obs.acquired_at == datetime(2025, 6, 10, hour, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"acq_time": "2460"}, "invalid_acquisition_datetime"),
        ({"acq_time": "12.5"}, "invalid_acquisition_datetime"),
        ({"acq_date": "2025-02-30"}, "invalid_acquisition_datetime"),
        ({"acq_date": "2025-06-11"}, "outside_requested_utc_window"),
        ({"latitude": "91"}, "invalid_coordinate"),
        ({"longitude": "NaN"}, "invalid_coordinate"),
        ({"latitude": "32"}, "outside_requested_bbox"),
        ({"frp": "-1"}, "invalid_frp"),
        ({"frp": "NaN"}, "invalid_frp"),
        ({"satellite": "N21"}, "sensor_product_mismatch"),
        ({"instrument": "MODIS"}, "unexpected_instrument"),
        ({"version": "2.0"}, "processing_product_mismatch"),
    ],
)
def test_rejection_reasons(tmp_path, changes, reason):
    result = normalized(tmp_path, **changes)
    assert not result.revisions
    assert result.report.rejection_counts[reason] == 1
    assert result.rejections[0].raw_values == ROW | changes


@pytest.mark.parametrize(
    "raw,expected",
    [("l", "low"), ("n", "nominal"), ("h", "high"), ("95", "unknown"), ("", "unknown")],
)
def test_confidence_categories_are_not_percentages(tmp_path, raw, expected):
    obs = normalized(tmp_path, confidence=raw).revisions[0].observation
    assert obs.confidence == expected
    assert obs.confidence_raw == raw


def test_frp_policy_and_optional_measurements(tmp_path):
    assert normalized(tmp_path, frp="0").revisions[0].observation.frp_mw == 0
    assert normalized(tmp_path, frp="").revisions[0].observation.frp_mw is None
    raw = raw_batch(
        tmp_path, [ROW | dict(frp="-1", scan="NaN", track="-2", bright_ti4="bad", daynight="X")]
    )
    result = normalize_firms_batch(raw, NormalizationPolicy(invalid_frp="null"))
    obs = result.revisions[0].observation
    assert obs.frp_mw is obs.scan is obs.track is obs.brightness_k is obs.daynight is None
    assert "frp_invalid" in obs.quality_flags
    assert obs.raw_values["provider_row"]["frp"] == "-1"
    assert (
        normalize_firms_batch(
            raw_batch(tmp_path, [ROW | {"frp": ""}]), NormalizationPolicy(missing_frp="reject")
        ).report.rejected_count
        == 1
    )


def test_identity_precision_and_correction_lineage(tmp_path):
    first = normalized(tmp_path).revisions[0]
    padded = normalized(tmp_path, latitude="34.123450", acq_time="37").revisions[0]
    assert first.observation.dedup_key == padded.observation.dedup_key
    nearby = normalized(tmp_path, latitude="34.123451").revisions[0]
    assert first.observation.dedup_key != nearby.observation.dedup_key
    correction = normalized(tmp_path, frp="18.1").revisions[0]
    assert first.observation.dedup_key == correction.observation.dedup_key
    assert first.revision_hash != correction.revision_hash
    other = normalize_firms_batch(
        raw_batch(tmp_path, [ROW | {"satellite": "N21"}], product=Product.NOAA21_NRT)
    ).revisions[0]
    assert first.observation.dedup_key != other.observation.dedup_key
    science = normalize_firms_batch(
        raw_batch(tmp_path, [ROW | {"version": "2.0"}], product=Product.NOAA20_SP)
    ).revisions[0]
    assert first.physical_key == science.physical_key
    assert first.observation.dedup_key != science.observation.dedup_key


def test_duplicate_variants_and_small_report(tmp_path):
    rows = [dict(ROW), dict(ROW), ROW | {"frp": "13"}] + [ROW | {"latitude": "bad"}] * 30
    raw = raw_batch(tmp_path, rows)
    before = raw.asset.storage_path
    result = normalize_firms_batch(raw)
    assert (
        result.report.accepted_count,
        result.report.duplicate_count,
        result.report.revised_count,
    ) == (1, 2, 1)
    assert len(result.revisions) == 2
    assert result.revisions[0].row_numbers == [2, 3]
    summary, rejected = write_quality_reports(result, raw.manifest_path.parent)
    assert len(json.loads(summary.read_text())["rejection_samples"]) == 20
    assert len(rejected.read_text().splitlines()) == 30
    assert raw.asset.storage_path == before
    assert load_saved_firms_batch(raw.manifest_path).rows == rows


def test_manifest_checksum_and_filename_validation(tmp_path):
    raw = raw_batch(tmp_path)
    manifest = json.loads(raw.manifest_path.read_text())
    manifest["responses"][-1]["filename"] = "../response-02.bin"
    raw.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_saved_firms_batch(raw.manifest_path)
    raw = raw_batch(tmp_path)
    from pathlib import Path

    Path(raw.asset.storage_path).write_bytes(b"changed")
    with pytest.raises(ValueError):
        load_saved_firms_batch(raw.manifest_path)


def test_offline_cli_needs_no_settings_or_database(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "firewatch.cli.get_settings", lambda: pytest.fail("Offline command accessed settings")
    )
    raw = raw_batch(tmp_path)
    response = CliRunner().invoke(app, ["normalize-firms", "--manifest", str(raw.manifest_path)])
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["quality"]["accepted_count"] == 1
    assert json.loads(response.output)["persisted"] is False


@pytest.mark.parametrize("alias", ["N20", "20", "1", "NOAA-20", "JPSS-1", "J1"])
def test_noaa20_provider_aliases_have_one_identity(tmp_path, alias):
    reference = normalized(tmp_path).revisions[0].observation
    obs = normalized(tmp_path, satellite=alias).revisions[0].observation
    assert obs.dedup_key == reference.dedup_key


def test_exact_bbox_decimal_boundary_and_metadata_consistency(tmp_path):
    result = normalized(tmp_path, longitude="74.00000000000000000001")
    assert result.report.rejection_counts["outside_requested_bbox"] == 1
    raw = raw_batch(tmp_path)
    raw.asset.retrieved_at = NOW.replace(hour=1)
    with pytest.raises(ValueError, match="metadata"):
        normalize_firms_batch(raw)
    raw = raw_batch(tmp_path)
    manifest = json.loads(raw.manifest_path.read_text())
    manifest["retrieved_at"] = NOW.replace(hour=1).isoformat()
    raw.manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="timestamp"):
        load_saved_firms_batch(raw.manifest_path)


def test_science_status_and_optional_raw_values_preserved(tmp_path):
    obs = (
        normalize_firms_batch(
            raw_batch(tmp_path, [ROW | {"version": "2.0"}], product=Product.NOAA20_SP)
        )
        .revisions[0]
        .observation
    )
    assert obs.raw_values["normalization"]["processing_status"] == "SP"
    assert obs.brightness_k == 330.2
    assert obs.raw_values["normalization"]["bright_ti5_k"] == 290.1
    assert obs.scan == 0.4 and obs.track == 0.5 and obs.daynight == "N"
    assert obs.raw_values["provider_row"]["version"] == "2.0"


@pytest.mark.parametrize("status", ["failed", "partial", "started"])
def test_unfinished_source_manifest_refused(tmp_path, status):
    raw = raw_batch(tmp_path)
    manifest = json.loads(raw.manifest_path.read_text())
    manifest["status"] = status
    raw.manifest_path.write_text(json.dumps(manifest))
    response = CliRunner().invoke(app, ["normalize-firms", "--manifest", str(raw.manifest_path)])
    assert response.exit_code == 1
