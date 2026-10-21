"""Versioned FIRMS source refresh contracts; no pipeline completion implied."""

from datetime import datetime, timedelta
from typing import Annotated, Literal, Self
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from firewatch.schemas.common import DTO, Product, UTCDateTime
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.firms import FirmsAreaRequest
from firewatch.sources.firms_normalize import NormalizationPolicy, digest

REFRESH_VERSION = "firms-refresh-v1"
Coverage = Literal["complete", "partial", "unavailable"]


class RefreshSpec(DTO):
    schema_version: Literal["firms-refresh-v1"] = "firms-refresh-v1"
    normalizer_version: Literal["firms-normalizer-v1"] = "firms-normalizer-v1"
    observation_key_version: Literal["firms-observation-v1"] = "firms-observation-v1"
    region: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
    as_of: UTCDateTime
    products: list[Product] = Field(
        default_factory=lambda: [Product.NOAA20_NRT, Product.NOAA21_NRT], min_length=1, max_length=2
    )
    chunk_days: int = Field(default=5, ge=1, le=5, strict=True)
    normalization_policy: NormalizationPolicy = Field(default_factory=NormalizationPolicy)
    request_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")] | None = None

    @model_validator(mode="after")
    def live_products(self) -> Self:
        if len(set(self.products)) != len(self.products) or any(
            p not in {Product.NOAA20_NRT, Product.NOAA21_NRT} for p in self.products
        ):
            raise ValueError("Choose distinct allowlisted NRT products")
        self.products = sorted(self.products, key=lambda p: p.value)
        self.as_of - timedelta(hours=48)
        return self

    @property
    def window_start(self) -> datetime:
        return self.as_of - timedelta(hours=48)

    def identity(self, region_id: UUID) -> UUID:
        token = self.request_key or digest(self.model_dump(mode="json"))
        return uuid5(NAMESPACE_URL, f"{REFRESH_VERSION}:{region_id}:{token}")

    def requests(self, bbox: BoundingBox) -> list[FirmsAreaRequest]:
        first = self.window_start.date()
        remaining = (self.as_of.date() - first).days + 1
        result = []
        for product in self.products:
            offset = 0
            while offset < remaining:
                days = min(self.chunk_days, remaining - offset)
                result.append(
                    FirmsAreaRequest(
                        product=product,
                        bbox=bbox,
                        start_date=first + timedelta(days=offset),
                        days=days,
                    )
                )
                offset += days
        return result


class RefreshCounts(DTO):
    fetched: int = Field(ge=0)
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    duplicate: int = Field(ge=0)


class RefreshChunk(DTO):
    key: str
    request: FirmsAreaRequest
    status: Literal["pending", "running", "succeeded", "failed"] = "pending"
    attempts: int = 0
    fetch_id: UUID | None = None
    batch_id: UUID | None = None
    attempted_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    retry_at: UTCDateTime | None = None
    error_code: str | None = None
    counts: RefreshCounts | None = None
    latest_acquisition_at: UTCDateTime | None = None
    fetch_manifest_path: str | None = None


class RefreshState(DTO):
    schema_version: Literal["firms-refresh-state-v1"] = "firms-refresh-state-v1"
    refresh_id: UUID
    region_id: UUID
    bbox: BoundingBox
    spec: RefreshSpec
    updated_at: UTCDateTime
    chunks: list[RefreshChunk]


class RefreshProductStatus(DTO):
    product: Product
    coverage: Coverage
    completed_chunks: int
    total_chunks: int
    counts: RefreshCounts | None
    known_successful_counts: RefreshCounts
    latest_acquisition_at: UTCDateTime | None
    last_successful_retrieval_at: UTCDateTime | None
    retry_at: UTCDateTime | None


class RefreshSummary(DTO):
    schema_version: Literal["firms-refresh-summary-v1"] = "firms-refresh-summary-v1"
    refresh_id: UUID
    region_id: UUID
    as_of: UTCDateTime
    window_start: UTCDateTime
    window_end: UTCDateTime
    evaluated_at: UTCDateTime
    coverage: Coverage
    products: list[RefreshProductStatus]


def summarize_refresh(state: RefreshState) -> RefreshSummary:
    products = []
    for product in state.spec.products:
        chunks = [c for c in state.chunks if c.request.product == product]
        completed = [c for c in chunks if c.status == "succeeded" and c.counts is not None]
        known = RefreshCounts(fetched=0, accepted=0, rejected=0, duplicate=0)
        for chunk in completed:
            assert chunk.counts is not None
            for field in ("fetched", "accepted", "rejected", "duplicate"):
                setattr(known, field, getattr(known, field) + getattr(chunk.counts, field))
        clean = len(completed) == len(chunks) and known.rejected == 0
        usable = any(
            c.counts is not None
            and (c.counts.fetched == 0 or c.counts.accepted + c.counts.duplicate > 0)
            for c in completed
        )
        coverage: Coverage = "complete" if clean else "partial" if usable else "unavailable"
        products.append(
            RefreshProductStatus(
                product=product,
                coverage=coverage,
                completed_chunks=len(completed),
                total_chunks=len(chunks),
                counts=known if len(completed) == len(chunks) else None,
                known_successful_counts=known,
                latest_acquisition_at=max(
                    (c.latest_acquisition_at for c in completed if c.latest_acquisition_at),
                    default=None,
                ),
                last_successful_retrieval_at=max(
                    (c.completed_at for c in completed if c.completed_at), default=None
                ),
                retry_at=min(
                    (c.retry_at for c in chunks if c.status == "failed" and c.retry_at),
                    default=None,
                ),
            )
        )
    overall: Coverage = (
        "complete"
        if all(p.coverage == "complete" for p in products)
        else "unavailable"
        if all(p.coverage == "unavailable" for p in products)
        else "partial"
    )
    return RefreshSummary(
        refresh_id=state.refresh_id,
        region_id=state.region_id,
        as_of=state.spec.as_of,
        window_start=state.spec.window_start,
        window_end=state.spec.as_of,
        evaluated_at=state.updated_at,
        coverage=overall,
        products=products,
    )
