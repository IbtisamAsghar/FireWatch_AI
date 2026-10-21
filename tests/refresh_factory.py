"""Synthetic provider-shaped refresh responses, never real smoke evidence."""

import csv
import hashlib
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from firms_factory import ROW

from firewatch.schemas.contracts import SourceAssetInput
from firewatch.sources.firms import FirmsAreaRequest, FirmsBatch

NOW = datetime(2025, 6, 12, 12, tzinfo=UTC)


class FakeRefreshClient:
    def __init__(self, path: Path, clock=lambda: NOW):
        self.output_dir = path.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.calls = []
        self.failures = {}
        self.empty = set()
        self.latitude = "34.12345"
        self.frp = "12.4"
        self.invalid_first = False

    def fetch(self, request: FirmsAreaRequest, *, batch_id: UUID | None = None) -> FirmsBatch:
        self.calls.append((request.product, request.start_date))
        failures = self.failures.get((request.product, request.start_date), [])
        if failures:
            raise failures.pop(0)
        identifier = batch_id or uuid4()
        folder = self.output_dir / str(identifier)
        folder.mkdir()
        rows = (
            []
            if request.product in self.empty
            else [
                ROW
                | dict(
                    acq_date=str(request.start_date + timedelta(days=i)),
                    acq_time="1200",
                    satellite="N20" if "NOAA20" in request.product else "N21",
                    latitude=self.latitude,
                    frp="-1" if self.invalid_first and i == 0 else self.frp,
                )
                for i in range(request.days)
            ]
        )
        content = io.StringIO(newline="")
        writer = csv.DictWriter(content, fieldnames=list(ROW))
        writer.writeheader()
        writer.writerows(rows)
        body = content.getvalue().encode()
        path = folder / "response-02.bin"
        path.write_bytes(body)
        retrieved = self.clock()
        asset = SourceAssetInput(
            provider="firms",
            product=request.product.value,
            request_metadata=request.model_dump(mode="json"),
            retrieved_at=retrieved,
            storage_path=str(path),
            content_hash=hashlib.sha256(body).hexdigest(),
            format="csv",
            processing_version="2.0NRT",
        )
        manifest = dict(
            schema_version="firms-request-manifest-v1",
            status="succeeded" if rows else "empty",
            batch_id=str(identifier),
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
                    byte_count=len(body),
                    sha256=asset.content_hash,
                )
            ],
        )
        target = folder / "manifest.json"
        target.write_text(json.dumps(manifest), encoding="utf-8")
        return FirmsBatch(
            batch_id=identifier,
            request=request,
            retrieved_at=retrieved,
            asset=asset,
            columns=list(ROW),
            rows=rows,
            row_count=len(rows),
            empty=not rows,
            processing_versions=["2.0NRT"] if rows else [],
            manifest_path=target,
        )
