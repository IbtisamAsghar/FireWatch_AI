"""Provider-shaped synthetic fixtures shared by normalization acceptance tests."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime
from uuid import uuid4

from firewatch.schemas.common import Product
from firewatch.schemas.contracts import SourceAssetInput
from firewatch.sources.firms import FirmsAreaRequest, FirmsBatch

ROW = dict(
    latitude="34.12345",
    longitude="71.12345",
    bright_ti4="330.2",
    scan="0.4",
    track="0.5",
    acq_date="2025-06-10",
    acq_time="0037",
    satellite="N20",
    instrument="VIIRS",
    confidence="n",
    version="2.0NRT",
    bright_ti5="290.1",
    frp="12.4",
    daynight="N",
)
NOW = datetime(2025, 6, 11, tzinfo=UTC)


def raw_batch(tmp_path, rows=None, *, product=Product.NOAA20_NRT, retrieved=NOW, bbox=None):
    rows = [dict(ROW)] if rows is None else rows
    folder = tmp_path / uuid4().hex
    folder.mkdir()
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=list(ROW))
    writer.writeheader()
    writer.writerows(rows)
    content = stream.getvalue().encode()
    path = folder / "response-02.bin"
    path.write_bytes(content)
    request = FirmsAreaRequest.model_validate(
        dict(
            product=product,
            bbox=bbox or dict(west=70, south=33, east=74, north=36),
            start_date="2025-06-10",
            days=1,
        )
    )
    asset = SourceAssetInput(
        provider="firms",
        product=product,
        request_metadata=request.model_dump(mode="json"),
        retrieved_at=retrieved,
        storage_path=str(path.resolve()),
        content_hash=hashlib.sha256(content).hexdigest(),
        format="csv",
        processing_version="2.0NRT",
    )
    batch = FirmsBatch(
        batch_id=uuid4(),
        request=request,
        retrieved_at=retrieved,
        asset=asset,
        columns=list(ROW),
        rows=rows,
        row_count=len(rows),
        empty=not rows,
        processing_versions=sorted({r["version"] for r in rows}),
        manifest_path=folder / "manifest.json",
    )
    manifest = dict(
        schema_version="firms-request-manifest-v1",
        status="succeeded" if rows else "empty",
        batch_id=str(batch.batch_id),
        request=request.model_dump(mode="json"),
        asset=asset.model_dump(mode="json"),
        retrieved_at=retrieved.isoformat(),
        row_count=len(rows),
        responses=[
            dict(
                kind="area",
                complete=True,
                http_status=200,
                filename=path.name,
                byte_count=len(content),
                sha256=asset.content_hash,
            )
        ],
    )
    batch.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return batch
