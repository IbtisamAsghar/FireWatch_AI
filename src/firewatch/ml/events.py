"""Conservative thermal-activity groups for partitioning, never feature construction."""

import math
from collections import defaultdict
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Literal, Self

import numpy as np
from pydantic import Field, model_validator
from sklearn.neighbors import BallTree  # type: ignore[import-untyped]

from firewatch.processing.clusters import EARTH_RADIUS_KM, distance_km
from firewatch.schemas.common import DTO, Digest, Latitude, Longitude, UTCDateTime
from firewatch.sources.firms_normalize import digest


class EventSpec(DTO):
    schema_version: Literal["partition-event-spec-v1"] = "partition-event-spec-v1"
    radius_km: float = Field(default=4.0, gt=0)
    gap_hours: int = Field(default=48, gt=0)
    review_radius_km: float = Field(default=8.0, gt=0)
    review_gap_hours: int = Field(default=72, gt=0)
    max_observations: int = Field(default=100000, ge=1, le=100000)
    max_neighbor_links: int = Field(default=50000000, ge=1, le=50000000)

    @model_validator(mode="after")
    def versioned(self) -> Self:
        if (self.radius_km, self.gap_hours, self.review_radius_km, self.review_gap_hours) != (
            4,
            48,
            8,
            72,
        ):
            raise ValueError("Changed event matching requires a reviewed new event version")
        return self


class EventObservation(DTO):
    physical_key: Digest
    acquired_at: UTCDateTime
    latitude: Latitude
    longitude: Longitude
    revision_hashes: list[Digest] = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_revisions(self) -> Self:
        if len(set(self.revision_hashes)) != len(self.revision_hashes):
            raise ValueError("Duplicate observation revision alias")
        return self


class EventGroup(DTO):
    event_id: Digest
    physical_keys: list[Digest]
    start: UTCDateTime
    end: UTCDateTime
    latitude: Latitude
    longitude: Longitude
    max_centroid_radius_km: float = Field(ge=0)
    review_flags: list[str]


class NearMiss(DTO):
    left: Digest
    right: Digest
    distance_km: float = Field(ge=0)
    gap_hours: float = Field(ge=0)


class EventCatalog(DTO):
    schema_version: Literal["partition-event-catalog-v1"] = "partition-event-catalog-v1"
    spec: EventSpec
    source_manifest_sha256: Digest
    observations: list[EventObservation]
    groups: list[EventGroup]
    candidate_links: int
    matched_links: int
    near_misses: list[NearMiss]
    near_misses_truncated: bool

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


class EventDecision(DTO):
    decision: Literal["accept", "exclude"]
    reason: str = Field(min_length=1, max_length=1000)


class SeparateDecision(DTO):
    left: Digest
    right: Digest
    reason: str = Field(min_length=1, max_length=1000)


class EventReview(DTO):
    schema_version: Literal["partition-event-review-v1"] = "partition-event-review-v1"
    catalog_sha256: Digest
    reviewer: str = Field(min_length=1, max_length=100)
    decisions: dict[Digest, EventDecision]
    merge_sets: list[list[Digest]] = Field(default_factory=list, max_length=1000)
    separate_pairs: list[SeparateDecision] = Field(default_factory=list, max_length=1000)
    note: str = Field(min_length=1, max_length=2000)


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def join(self, left: int, right: int) -> None:
        left, right = self.find(left), self.find(right)
        self.parents[max(left, right)] = min(left, right)


def _group(rows: list[EventObservation], spec: EventSpec) -> EventGroup:
    points = [(math.radians(r.latitude), math.radians(r.longitude)) for r in rows]
    x = math.fsum(math.cos(a) * math.cos(b) for a, b in points)
    y = math.fsum(math.cos(a) * math.sin(b) for a, b in points)
    z = math.fsum(math.sin(a) for a, b in points)
    if math.sqrt(x * x + y * y + z * z) < 1e-12:
        raise ValueError("Degenerate event centroid")
    lat, lon = math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))
    radius = max(distance_km(lat, lon, r.latitude, r.longitude) for r in rows)
    start, end = min(r.acquired_at for r in rows), max(r.acquired_at for r in rows)
    flags = []
    if radius > 50:
        flags.append("wide_connected_component")
    if end - start > timedelta(days=30):
        flags.append("long_connected_component")
    if len(rows) > 5000:
        flags.append("large_connected_component")
    keys = sorted(r.physical_key for r in rows)
    return EventGroup(
        event_id=digest(dict(spec=spec.model_dump(mode="json"), physical_keys=keys)),
        physical_keys=keys,
        start=start,
        end=end,
        latitude=lat,
        longitude=lon,
        max_centroid_radius_km=radius,
        review_flags=flags,
    )


def build_event_catalog(
    observations: list[EventObservation], source_manifest_sha256: str, spec: EventSpec | None = None
) -> EventCatalog:
    spec = EventSpec.model_validate((spec or EventSpec()).model_dump())
    rows = sorted(
        [EventObservation.model_validate(r.model_dump()) for r in observations],
        key=lambda r: (r.acquired_at, r.physical_key),
    )
    if len(rows) > spec.max_observations or len({r.physical_key for r in rows}) != len(rows):
        raise ValueError("Duplicate or oversized event observation input")
    aliases = [key for r in rows for key in r.revision_hashes]
    if len(set(aliases)) != len(aliases):
        raise ValueError("Revision alias cannot refer to different physical observations")
    for row in rows:
        row.revision_hashes.sort()
    # Day-indexed spatial trees avoid an unbounded seasonal neighbor list and
    # prevent dense observations months apart from exhausting the temporal work budget.
    buckets: dict[date, list[int]] = defaultdict(list)
    for i, row in enumerate(rows):
        buckets[row.acquired_at.date()].append(i)
    trees = {
        day: BallTree(
            np.radians([[rows[i].latitude, rows[i].longitude] for i in ids]), metric="haversine"
        )
        for day, ids in buckets.items()
    }
    uf = UnionFind(len(rows))
    candidates, matched = 0, 0

    def neighbors(index: int, radius_km: float, gap_hours: int) -> Iterator[int]:
        row = rows[index]
        first = (row.acquired_at - timedelta(hours=gap_hours)).date()
        for day in sorted(buckets):
            if not first <= day <= row.acquired_at.date():
                continue
            ids = buckets[day]
            found = trees[day].query_radius(
                np.radians([[row.latitude, row.longitude]]),
                r=np.nextafter(radius_km / EARTH_RADIUS_KM, math.inf),
            )[0]
            for value in found:
                j = ids[int(value)]
                if j < index and row.acquired_at - rows[j].acquired_at <= timedelta(
                    hours=gap_hours
                ):
                    yield j

    for i, row in enumerate(rows):
        for j in neighbors(i, spec.radius_km, spec.gap_hours):
            candidates += 1
            if candidates > spec.max_neighbor_links:
                raise ValueError("Event neighborhood bound exceeded")
            if (
                distance_km(row.latitude, row.longitude, rows[j].latitude, rows[j].longitude)
                <= spec.radius_km
            ):
                uf.join(i, j)
                matched += 1
    components: dict[int, list[EventObservation]] = defaultdict(list)
    for i, row in enumerate(rows):
        components[uf.find(i)].append(row)
    groups = sorted(
        (_group(items, spec) for items in components.values()), key=lambda g: g.event_id
    )
    by_key = {key: g.event_id for g in groups for key in g.physical_keys}
    pairs: dict[tuple[str, str], NearMiss] = {}
    truncated = False
    for i, row in enumerate(rows):
        for j in neighbors(i, spec.review_radius_km, spec.review_gap_hours):
            candidates += 1
            if candidates > spec.max_neighbor_links:
                raise ValueError("Event neighborhood bound exceeded")
            if uf.find(i) == uf.find(j):
                continue
            distance = distance_km(row.latitude, row.longitude, rows[j].latitude, rows[j].longitude)
            if distance > spec.review_radius_km:
                continue
            gap = (row.acquired_at - rows[j].acquired_at).total_seconds() / 3600
            left, right = sorted((by_key[row.physical_key], by_key[rows[j].physical_key]))
            key = (left, right)
            if key not in pairs and len(pairs) >= 1000:
                truncated = True
                continue
            if key not in pairs or (gap, distance) < (pairs[key].gap_hours, pairs[key].distance_km):
                pairs[key] = NearMiss(left=left, right=right, distance_km=distance, gap_hours=gap)
    return EventCatalog(
        spec=spec,
        source_manifest_sha256=source_manifest_sha256,
        observations=rows,
        groups=groups,
        candidate_links=candidates,
        matched_links=matched,
        near_misses=[pairs[k] for k in sorted(pairs)],
        near_misses_truncated=truncated,
    )


def reviewed_groups(
    catalog: EventCatalog, review: EventReview | None
) -> tuple[dict[str, str], dict[str, tuple[EventGroup, str]]]:
    groups = catalog.groups
    ids = [g.event_id for g in groups]
    lookup = {key: i for i, key in enumerate(ids)}
    if review and (
        review.catalog_sha256 != catalog.sha256 or not set(review.decisions) <= set(ids)
    ):
        raise ValueError("Event review is not bound to this catalog")
    uf = UnionFind(len(groups))
    if review:
        seen: set[str] = set()
        for merge in review.merge_sets:
            if len(set(merge)) != len(merge) or len(merge) < 2 or not set(merge) <= set(ids):
                raise ValueError("Invalid reviewed event merge")
            if seen.intersection(merge):
                raise ValueError("Merge sets must be disjoint")
            seen.update(merge)
            for key in merge[1:]:
                uf.join(lookup[merge[0]], lookup[key])
    components: dict[int, list[EventGroup]] = defaultdict(list)
    for i, g in enumerate(groups):
        components[uf.find(i)].append(g)
    mapping, effective = {}, {}
    obs = {r.physical_key: r for r in catalog.observations}
    for parts in components.values():
        group = (
            parts[0]
            if len(parts) == 1
            else _group([obs[k] for g in parts for k in g.physical_keys], catalog.spec)
        )
        decisions = [review.decisions.get(g.event_id) if review else None for g in parts]
        state = (
            "exclude"
            if any(d and d.decision == "exclude" for d in decisions)
            else "pending"
            if any(d is None for d in decisions)
            else "accept"
        )
        effective[group.event_id] = (group, state)
        for part in parts:
            mapping[part.event_id] = group.event_id
    separations = set()
    if review:
        candidates = {tuple(sorted((p.left, p.right))) for p in catalog.near_misses}
        for decision in review.separate_pairs:
            pair = tuple(sorted((decision.left, decision.right)))
            if (
                pair not in candidates
                or pair in separations
                or mapping[pair[0]] == mapping[pair[1]]
            ):
                raise ValueError("Invalid, duplicate or merged event separation decision")
            separations.add(pair)
    pending: set[str] = set()
    for link in catalog.near_misses:
        left, right = mapping[link.left], mapping[link.right]
        if (
            left != right
            and effective[left][1] == effective[right][1] == "accept"
            and tuple(sorted((link.left, link.right))) not in separations
        ):
            pending.update((left, right))
    for key in pending:
        effective[key] = (effective[key][0], "pending")
    return mapping, effective
