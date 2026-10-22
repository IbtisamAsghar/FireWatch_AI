"""Arrow null/zero semantics and irreversible child-process network isolation."""

import json
import subprocess
import sys
from datetime import UTC, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from feature_factory import inputs, observation

from firewatch.pipeline.handoff import _safe, feature_schema
from firewatch.processing.features import build_features, model_inputs


@pytest.mark.parametrize("frp", [None, 0.0])
def test_parquet_preserves_null_and_real_zero(tmp_path, frp):
    batch, rows, enrichment, sat = inputs([observation(1, frp=frp)])
    feature = build_features(batch, rows, enrichment, sat)[0]
    snap = batch.snapshots[0].snapshot
    row = dict(
        run_id=str(feature.run_id),
        snapshot_id=str(feature.snapshot_id),
        region_id="synthetic",
        mode="replay",
        feature_version=feature.feature_version,
        feature_hash=feature.feature_hash,
        as_of=feature.as_of,
        latest_detection_at=snap.latest_detection_at,
        processed_at=datetime.now(UTC),
        latitude=snap.latitude,
        longitude=snap.longitude,
        member_count=1,
        lineage_json=json.dumps(feature.lineage),
        **model_inputs(feature),
    )
    path = tmp_path / "features.parquet"
    pq.write_table(pa.Table.from_pylist([row], schema=feature_schema()), path)
    saved = pq.read_table(path).to_pylist()[0]
    assert saved["frp_max_mw"] == frp
    assert saved["frp_max_mw_missing"] == (frp is None)
    assert str(pq.read_schema(path).field("as_of").type) == "timestamp[us, tz=UTC]"


@pytest.mark.parametrize(
    "value",
    [
        {"storage_path": "hidden"},
        {"note": "C:\\Users\\private"},
        {"note": "https://example.invalid"},
        {"note": "test-only-secret-value"},
    ],
)
def test_plaintext_metadata_screening_precedes_compression(value):
    with pytest.raises(ValueError):
        _safe(value, ["test-only-secret-value"])


def test_outbound_network_guard_is_confined_to_subprocess():
    code = """
import socket,httpx
from firewatch.pipeline.network_guard import install_database_only_network_guard
attempts=install_database_only_network_guard('postgresql+psycopg://test:password@127.0.0.1:5432/firewatch_test')
for operation in (lambda:socket.getaddrinfo('example.invalid',443),
                  lambda:httpx.Client().get('https://example.invalid')):
 try: operation()
 except PermissionError: pass
 else: raise RuntimeError('Network guard failed')
assert len(attempts)==2
print('guard_verified')
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "guard_verified"


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"a":NaN}',
        '{"a":Infinity}',
        '{"a":-Infinity}',
    ],
)
def test_handoff_json_refuses_ambiguous_or_nonfinite_values(raw):
    from firewatch.pipeline.handoff import _decode_json

    with pytest.raises(ValueError):
        _decode_json(raw)


def test_parquet_required_nulls_are_rejected(tmp_path):
    from firewatch.pipeline.handoff import _table

    schema = pa.schema([pa.field("required", pa.string(), nullable=False)])
    path = tmp_path / "invalid.parquet"
    # Arrow's table constructor permits this despite the declared nullability.
    table = pa.Table.from_pylist([{"required": None}], schema=schema)
    # Some Arrow versions reject writing nonnullable nulls before the verifier runs.
    try:
        pq.write_table(table, path)
    except pa.ArrowException:
        return
    with pytest.raises(ValueError, match="required"):
        _table(path, schema, 1)


@pytest.mark.parametrize("body", [[], None, {}, {"schema_version": "pipeline-handoff-v1"}])
def test_malformed_handoff_reports_validation_error(tmp_path, body):
    from firewatch.pipeline.handoff import verify_handoff

    (tmp_path / "manifest.json").write_text(json.dumps(body))
    with pytest.raises(ValueError):
        verify_handoff(tmp_path)
