"""Deterministic, bounded DBSCAN snapshots from selected FIRMS revisions."""

import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid5

import numpy as np
import sklearn  # type: ignore[import-untyped]
import yaml
from pydantic import Field, model_validator
from sklearn.cluster import DBSCAN  # type: ignore[import-untyped]
from sklearn.neighbors import BallTree  # type: ignore[import-untyped]

from firewatch.repositories.revisions import SelectedDetection
from firewatch.schemas.common import DTO, Confidence, Digest, RunMode, Sensor, UTCDateTime
from firewatch.schemas.contracts import ClusterSnapshot
from firewatch.sources.firms_normalize import digest
from firewatch.sources.open_meteo import utc

EARTH_RADIUS_KM = 6371.0088
ALGORITHM = "dbscan-haversine-v1"


class ClusteringSpec(DTO):
    algorithm_version: str = Field(default=ALGORITHM, pattern=r"^[A-Za-z0-9_-]{1,80}$")
    radius_km: float = Field(default=2, gt=0, le=100)
    candidate_hours: Literal[6] = 6
    activity_hours: tuple[Literal[6], Literal[12], Literal[24]] = (6, 12, 24)
    min_samples: Literal[1] = 1
    earth_radius_km: float = EARTH_RADIUS_KM
    centroid: Literal["spherical-mean-v1"] = "spherical-mean-v1"
    max_history: int = Field(default=100000, ge=1, le=500000)
    max_candidates: int = Field(default=10000, ge=1, le=100000)
    max_neighbor_links: int = Field(default=1000000, ge=1, le=10000000)

    @model_validator(mode="after")
    def versioned(self) -> Self:
        if self.earth_radius_km != EARTH_RADIUS_KM:
            raise ValueError("This processor requires the versioned mean Earth radius")
        if self.algorithm_version == ALGORITHM and self.radius_km != 2:
            raise ValueError("A changed radius requires a new algorithm version")
        return self


def load_clustering_spec(path: Path) -> ClusteringSpec:
    with path.open("rb") as source:
        raw = source.read(65537)
    if len(raw) > 65536:
        raise ValueError("Feature configuration exceeds bound")
    config = yaml.safe_load(raw)
    if not isinstance(config, dict) or config.get("schema_version") != "features-v1":
        raise ValueError("Expected features-v1 configuration")
    return ClusteringSpec(
        algorithm_version=config["cluster_algorithm_version"],
        radius_km=config["cluster_radius_km"],
        candidate_hours=config["snapshot_lookback_hours"],
        activity_hours=tuple(config["activity_lookbacks_hours"]),
    )


class ClusterDetails(DTO):
    snapshot: ClusterSnapshot
    sensor_counts: dict[str, int]
    confidence_counts: dict[str, int]
    activity_counts: dict[str, int]
    frp_valid_members: int = Field(ge=0)
    frp_missing_members: int = Field(ge=0)
    member_radius_max_km: float = Field(ge=0)
    quality_flags: list[str] = Field(default_factory=list)


class SnapshotBatch(DTO):
    schema_version: Literal["cluster-snapshots-v1"] = "cluster-snapshots-v1"
    run_id: UUID
    mode: RunMode
    as_of: UTCDateTime
    feature_version: str
    config: ClusteringSpec
    input_hash: Digest
    history_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    snapshots: list[ClusterDetails]
    library_versions: dict[str, str]
    quality_flags: list[str] = Field(default_factory=list)


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    for lat, lon in [(lat1, lon1), (lat2, lon2)]:
        if (
            not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90 <= lat <= 90
            or not -180 <= lon <= 180
        ):
            raise ValueError("Invalid spherical coordinates")
    a, b = math.radians(lat1), math.radians(lat2)
    dlat, dlon = b - a, math.radians(lon2 - lon1)
    term = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(min(1, max(0, term))))


def centroid(rows: list[SelectedDetection]) -> tuple[float, float]:
    if not rows:
        raise ValueError("A centroid needs members")
    points = [
        (math.radians(r.observation.latitude), math.radians(r.observation.longitude)) for r in rows
    ]
    x = math.fsum(math.cos(lat) * math.cos(lon) for lat, lon in points) / len(points)
    y = math.fsum(math.cos(lat) * math.sin(lon) for lat, lon in points) / len(points)
    z = math.fsum(math.sin(lat) for lat, _ in points) / len(points)
    if math.sqrt(x * x + y * y + z * z) < 1e-12:
        raise ValueError("Spherical centroid is degenerate")
    return (
        math.degrees(math.atan2(z, math.hypot(x, y))),
        (math.degrees(math.atan2(y, x)) + 180) % 360 - 180,
    )


def build_snapshots(
    run_id: UUID,
    mode: RunMode,
    as_of: datetime,
    selected: list[SelectedDetection],
    config: ClusteringSpec | None = None,
    *,
    feature_version: str = "features-v1",
) -> SnapshotBatch:
    """Shared by training, replay and live stages; no DB, clock, network or labels."""
    config = ClusteringSpec.model_validate((config or ClusteringSpec()).model_dump())
    cutoff = utc(as_of)
    if len(selected) > config.max_history:
        raise ValueError("Clustering input exceeds history bound")
    history = []
    for item in selected:
        row = SelectedDetection.model_validate(item.model_dump())
        if cutoff - timedelta(hours=24) < row.observation.acquired_at <= cutoff:
            if digest(row.observation.model_dump(mode="json")) != row.revision_hash:
                raise ValueError("Selected observation revision hash mismatch")
            normalization = row.observation.raw_values.get("normalization")
            if (
                not isinstance(normalization, dict)
                or normalization.get("physical_key") != row.physical_key
            ):
                raise ValueError("Selected observation physical identity mismatch")
            history.append(row)
    history.sort(key=lambda r: (r.observation.acquired_at, r.detection_id.hex))
    if len({r.detection_id for r in history}) != len(history) or len(
        {r.physical_key for r in history}
    ) != len(history):
        raise ValueError("Select one observation/revision per physical identity")
    for sensor in Sensor:
        if len({r.observation.product for r in history if r.observation.sensor == sensor}) > 1:
            raise ValueError("Select one product per sensor before clustering")
    candidates = [r for r in history if r.observation.acquired_at > cutoff - timedelta(hours=6)]
    if len(candidates) > config.max_candidates:
        raise ValueError("Clustering candidate bound exceeded")
    batch = SnapshotBatch(
        run_id=run_id,
        mode=mode,
        as_of=cutoff,
        feature_version=feature_version,
        config=config,
        input_hash=digest([r.model_dump(mode="json") for r in history]),
        history_count=len(history),
        candidate_count=len(candidates),
        snapshots=[],
        library_versions={"numpy": np.__version__, "scikit_learn": sklearn.__version__},
    )
    if not candidates:
        return batch
    coordinates = np.radians(
        np.asarray(
            [(r.observation.latitude, r.observation.longitude) for r in candidates],
            dtype=np.float64,
        )
    )
    eps = config.radius_km / EARTH_RADIUS_KM
    neighbors = BallTree(coordinates, metric="haversine").query_radius(
        coordinates, r=eps, count_only=True
    )
    if sum(int(n) for n in neighbors) > config.max_neighbor_links:
        raise ValueError("DBSCAN neighborhood memory bound exceeded")
    labels = DBSCAN(
        eps=eps, min_samples=1, metric="haversine", algorithm="ball_tree", n_jobs=1
    ).fit_predict(coordinates)
    groups: dict[int, list[SelectedDetection]] = {}
    for label, row in zip(labels, candidates, strict=True):
        groups.setdefault(int(label), []).append(row)
    history_tree = BallTree(
        np.radians(
            np.asarray(
                [(r.observation.latitude, r.observation.longitude) for r in history],
                dtype=np.float64,
            )
        ),
        metric="haversine",
    )
    links = 0
    for members in groups.values():
        members.sort(key=lambda r: r.detection_id.hex)
        lat, lon = centroid(members)
        nearby = history_tree.query_radius(np.radians([[lat, lon]]), r=eps)[0]
        links += len(nearby)
        if links > config.max_neighbor_links:
            raise ValueError("Centroid activity neighborhood bound exceeded")
        activity = {
            f"detections_{hours}h": sum(
                history[int(i)].observation.acquired_at > cutoff - timedelta(hours=hours)
                for i in nearby
            )
            for hours in config.activity_hours
        }
        known = [r.observation.frp_mw for r in members if r.observation.frp_mw is not None]
        flags = []
        if len(known) != len(members):
            flags.append("frp_missing_members")
            if known:
                flags.append("frp_aggregates_partial")
        radius = max(
            distance_km(lat, lon, r.observation.latitude, r.observation.longitude) for r in members
        )
        if radius > config.radius_km + 1e-9:
            flags.append("members_outside_centroid_radius")
        identity = (
            "cluster-snapshot-v1:"
            + digest(config.model_dump(mode="json"))
            + ":"
            + ",".join(r.detection_id.hex for r in members)
        )
        snapshot = ClusterSnapshot(
            run_id=run_id,
            mode=mode,
            as_of=cutoff,
            feature_version=feature_version,
            snapshot_id=uuid5(run_id, identity),
            latitude=lat,
            longitude=lon,
            member_ids=[r.detection_id for r in members],
            frp_max_mw=max(known) if known else None,
            frp_sum_mw=math.fsum(known) if known else None,
            latest_detection_at=max(r.observation.acquired_at for r in members),
            algorithm_version=config.algorithm_version,
        )
        sensor_counts = Counter(r.observation.sensor.value for r in members)
        confidence_counts = Counter(r.observation.confidence.value for r in members)
        batch.snapshots.append(
            ClusterDetails(
                snapshot=snapshot,
                sensor_counts={s.value: sensor_counts[s.value] for s in Sensor},
                confidence_counts={c.value: confidence_counts[c.value] for c in Confidence},
                activity_counts=activity,
                frp_valid_members=len(known),
                frp_missing_members=len(members) - len(known),
                member_radius_max_km=radius,
                quality_flags=flags,
            )
        )
    batch.snapshots.sort(key=lambda d: d.snapshot.snapshot_id.hex)
    return batch
