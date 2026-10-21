"""Synthetic, integrity-bound observation revisions for spatial/time boundary tests."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from firewatch.repositories.revisions import SelectedDetection
from firewatch.schemas.common import Confidence, Product, Sensor
from firewatch.schemas.contracts import DetectionInput
from firewatch.sources.firms_normalize import digest

T = datetime(2024, 7, 25, 12, tzinfo=UTC)
RUN = uuid5(NAMESPACE_URL, "synthetic-snapshot-run")


def selected(
    i,
    *,
    latitude=0.0,
    longitude=0.0,
    hours=0.0,
    frp=10.0,
    confidence=Confidence.HIGH,
    sensor=Sensor.NOAA20,
    acquired=None,
    product=None,
):
    acquired = acquired or T - timedelta(hours=hours)
    product = product or (Product.NOAA20_SP if sensor == Sensor.NOAA20 else Product.NOAA21_SP)
    physical = digest(
        dict(
            i=i,
            sensor=sensor,
            acquired=acquired.isoformat(),
            latitude=latitude,
            longitude=longitude,
        )
    )
    observation = DetectionInput(
        product=product,
        sensor=sensor,
        latitude=latitude,
        longitude=longitude,
        acquired_at=acquired,
        frp_mw=frp,
        confidence=confidence,
        dedup_key=digest(dict(product=product, key=physical)),
        raw_values={"normalization": {"physical_key": physical}},
    )
    return SelectedDetection(
        detection_id=uuid5(NAMESPACE_URL, "synthetic-detection-" + str(i)),
        batch_id=uuid5(NAMESPACE_URL, "synthetic-batch"),
        physical_key=physical,
        revision_hash=digest(observation.model_dump(mode="json")),
        observation=observation,
    )
