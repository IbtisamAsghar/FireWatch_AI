"""Trusted local archive provenance; all date intervals include both calendar dates."""

import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BeforeValidator, Field, field_validator, model_validator

from firewatch.schemas.common import DTO, Digest, Product, Sensor, UTCDateTime
from firewatch.schemas.contracts import BoundingBox
from firewatch.sources.firms import FirmsAreaRequest, date_input
from firewatch.sources.firms_normalize import NORMALIZER_VERSION, NormalizationPolicy, digest

ARCHIVE_VERSION = "firms-archive-import-v1"


class ArchiveError(ValueError):
    """Diagnostics never include provider values, local secrets or validation inputs."""


class DateInterval(DTO):
    start_date: Annotated[date, BeforeValidator(date_input)]
    end_date: Annotated[date, BeforeValidator(date_input)]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("Date interval is reversed")
        return self

    def contains(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date


class ArchiveManifest(DTO):
    schema_version: Literal["firms-archive-manifest-v1"] = "firms-archive-manifest-v1"
    trusted_local_input: Literal[True]
    source_url: Annotated[str, Field(max_length=1000)]
    source_kind: Literal["archive_portal", "area_api_sample"] = "archive_portal"
    archive_request_id: Annotated[str, Field(pattern=r"^[0-9]{1,20}$")] | None = None
    product: Product
    sensor: Sensor
    interval: DateInterval
    region: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
    bbox: BoundingBox
    sha256: Digest
    retrieved_at: UTCDateTime
    attribution_url: Annotated[str, Field(max_length=1000)]
    earliest_feature_cutoff: UTCDateTime
    last_label_cutoff: UTCDateTime
    cutoff_purpose: Literal["proposed", "approved"] = "proposed"
    covered_intervals: list[DateInterval] = Field(default_factory=list, max_length=1000)
    expected_active_intervals: list[DateInterval] = Field(default_factory=list, max_length=1000)
    confirmed_empty_intervals: list[DateInterval] = Field(default_factory=list, max_length=1000)

    @field_validator("source_url", "attribution_url")
    @classmethod
    def public_reference(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme != "https"
            or url.hostname
            not in {"firms.modaps.eosdis.nasa.gov", "www.earthdata.nasa.gov", "earthdata.nasa.gov"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.port not in {None, 443}
            or (
                url.hostname == "firms.modaps.eosdis.nasa.gov"
                and not (
                    url.path.startswith(("/download", "/content"))
                    or re.fullmatch(r"/data/download/DL_FIRE_J[12]V-C2_[0-9]+\.zip", url.path)
                )
            )
        ):
            raise ValueError(
                "Use a public NASA reference without authentication or query parameters"
            )
        return value

    @model_validator(mode="after")
    def consistent(self) -> Self:
        linked = re.fullmatch(
            r"/data/download/DL_FIRE_J([12])V-C2_([0-9]+)\.zip", urlsplit(self.source_url).path
        )
        if linked and (
            self.source_kind != "archive_portal"
            or self.archive_request_id != linked.group(2)
            or self.sensor != (Sensor.NOAA20 if linked.group(1) == "1" else Sensor.NOAA21)
        ):
            raise ValueError("Archive URL does not match its request, sensor or source kind")
        expected = Sensor.NOAA20 if "NOAA20" in self.product else Sensor.NOAA21
        if self.sensor != expected or self.earliest_feature_cutoff > self.last_label_cutoff:
            raise ValueError("Product/sensor or cutoff order is inconsistent")
        start = datetime.combine(self.interval.start_date, time.min, UTC)
        try:
            end = datetime.combine(self.interval.end_date + timedelta(days=1), time.min, UTC)
        except OverflowError:
            raise ValueError("Unsupported archive calendar interval") from None
        if (end - start).days > 3660:
            raise ValueError("Split archives longer than ten years")
        if start > self.earliest_feature_cutoff - timedelta(
            hours=24
        ) or end < self.last_label_cutoff + timedelta(hours=24):
            raise ValueError("Archive requires at least 24 hours before features and after labels")
        for interval in (
            self.covered_intervals + self.expected_active_intervals + self.confirmed_empty_intervals
        ):
            if (
                interval.start_date < self.interval.start_date
                or interval.end_date > self.interval.end_date
            ):
                raise ValueError("Coverage declarations must lie inside the archive interval")
        if self.interval.end_date > self.retrieved_at.date():
            raise ValueError("Archive interval extends beyond its retrieval date")
        self.request(self.interval.start_date)
        return self

    def request(self, day: date) -> FirmsAreaRequest:
        return FirmsAreaRequest(product=self.product, bbox=self.bbox, start_date=day, days=1)

    def days(self) -> list[date]:
        return [
            self.interval.start_date + timedelta(days=i)
            for i in range((self.interval.end_date - self.interval.start_date).days + 1)
        ]

    def covered(self, day: date) -> bool:
        return any(item.contains(day) for item in self.covered_intervals)

    def identity(self, policy: NormalizationPolicy) -> UUID:
        return uuid5(
            NAMESPACE_URL,
            digest(
                {
                    "version": ARCHIVE_VERSION,
                    "normalizer": NORMALIZER_VERSION,
                    "manifest": self.model_dump(mode="json"),
                    "policy": policy.model_dump(mode="json"),
                }
            ),
        )


def load_archive_manifest(path: Path) -> ArchiveManifest:
    if path.stat().st_size > 1024 * 1024:
        raise ArchiveError("Archive manifest exceeds its size bound.")
    return ArchiveManifest.model_validate_json(path.read_text(encoding="utf-8"))
