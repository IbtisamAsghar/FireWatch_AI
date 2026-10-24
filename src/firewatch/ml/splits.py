"""Chronological event-disjoint partitions and forward-only development folds."""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Literal, Self

from pydantic import Field, model_validator

from firewatch.ml.events import EventCatalog, EventReview, reviewed_groups
from firewatch.ml.labels import LabelResult, TargetInput, TargetSpec, on_cadence
from firewatch.processing.features import FeatureSpec, validate_feature_set
from firewatch.schemas.common import DTO, Digest, UTCDateTime
from firewatch.sources.firms_normalize import digest

Partition = Literal["train", "validation", "calibration", "threshold", "final_test"]
PARTITIONS: tuple[Partition, ...] = (
    "train",
    "validation",
    "calibration",
    "threshold",
    "final_test",
)


class TimeBlock(DTO):
    partition: Partition
    start: UTCDateTime
    end: UTCDateTime

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start >= self.end:
            raise ValueError("Partition block must be ordered [start,end)")
        return self


class SplitSpec(DTO):
    schema_version: Literal["event-temporal-split-v1"] = "event-temporal-split-v1"
    target_spec_sha256: Digest = TargetSpec().sha256
    feature_spec_sha256: Digest = digest(FeatureSpec().model_dump(mode="json"))
    feature_lookback_hours: Literal[24] = 24
    label_horizon_hours: Literal[24] = 24
    purge_hours_each_side: int = Field(default=48, ge=48, le=168)
    min_train_events: int = Field(default=3, ge=3, le=1000)
    min_eval_events: int = Field(default=2, ge=2, le=1000)
    min_rows_per_class: int = Field(default=5, ge=1, le=1000)
    min_cv_folds: int = Field(default=2, ge=2, le=3)
    blocks: list[TimeBlock]
    cv_boundaries: list[UTCDateTime]

    @model_validator(mode="after")
    def freeze(self) -> Self:
        if self.target_spec_sha256 != TargetSpec().sha256 or self.feature_spec_sha256 != digest(
            FeatureSpec().model_dump(mode="json")
        ):
            raise ValueError(
                "Split specification must freeze existing target and feature definitions"
            )
        if [b.partition for b in self.blocks] != list(PARTITIONS):
            raise ValueError(
                "Declare five chronological train/validation/calibration/threshold/test blocks"
            )
        if any(a.end > b.start for a, b in zip(self.blocks, self.blocks[1:], strict=False)):
            raise ValueError("Partition blocks overlap or are out of time order")
        if len(self.cv_boundaries) != 4 or self.cv_boundaries != sorted(set(self.cv_boundaries)):
            raise ValueError("Declare four ordered forward-CV boundaries")
        if (
            self.cv_boundaries[0] <= self.blocks[0].start
            or self.cv_boundaries[-1] != self.blocks[-1].start
        ):
            raise ValueError("CV must remain inside development and stop before final test")
        return self

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


def default_split_spec() -> SplitSpec:
    def dt(value: str) -> datetime:
        return datetime.fromisoformat(value).replace(tzinfo=UTC)

    dates = [
        ("2022-05-01", "2022-11-01"),
        ("2024-05-01", "2024-06-15"),
        ("2024-06-15", "2024-07-15"),
        ("2024-07-15", "2024-08-15"),
        ("2024-08-15", "2024-11-01"),
    ]
    return SplitSpec(
        blocks=[
            TimeBlock(partition=name, start=dt(start), end=dt(end))
            for name, (start, end) in zip(PARTITIONS, dates, strict=True)
        ],
        cv_boundaries=[dt(v) for v in ("2022-07-01", "2024-05-01", "2024-07-01", "2024-08-15")],
    )


class SplitSample(DTO):
    target: TargetInput
    label: LabelResult

    @model_validator(mode="after")
    def joined(self) -> Self:
        f = validate_feature_set(self.target.feature)
        if (
            self.label.run_id,
            self.label.snapshot_id,
            self.label.as_of,
            self.label.feature_hash,
        ) != (f.run_id, f.snapshot_id, f.as_of, f.feature_hash):
            raise ValueError("Split label must match the exact frozen feature snapshot")
        if (
            self.label.target_value
            != {"positive": 1, "negative": 0, "unknown": None}[self.label.label]
        ):
            raise ValueError("Split label class and target value disagree")
        if self.label.baseline_products != self.target.products:
            raise ValueError("Split label source products differ from current inputs")
        if self.label.target_spec_sha256 != TargetSpec().sha256:
            raise ValueError("Split label target mismatch")
        return self

    @property
    def row_id(self) -> str:
        f = self.target.feature
        return digest(
            dict(run_id=str(f.run_id), snapshot_id=str(f.snapshot_id), feature_hash=f.feature_hash)
        )


class Membership(DTO):
    row_id: Digest
    event_id: Digest | None
    as_of: UTCDateTime
    partition: Partition | None
    target_value: Literal[0, 1] | None
    exclusion_reasons: list[str]


class ForwardFold(DTO):
    fold_id: str
    boundary: UTCDateTime
    end: UTCDateTime
    train_ids: list[Digest]
    validation_ids: list[Digest]
    excluded_ids: list[Digest]
    sufficient: bool


class SplitResult(DTO):
    schema_version: Literal["event-temporal-membership-v1"] = "event-temporal-membership-v1"
    dataset_sha256: Digest
    spec_sha256: Digest
    catalog_sha256: Digest
    review_sha256: Digest | None
    membership: list[Membership]
    folds: list[ForwardFold]
    strategy: Literal["chronological", "grouped_temporal_cv", "baseline_only"]
    training_ready: bool
    development_cv_ready: bool
    final_holdout_retained: bool
    final_holdout_usable: bool
    final_test_status: Literal["sealed"] = "sealed"
    release_recommendation: Literal["baseline_only", "await_model_evaluation"]
    partition_counts: dict[str, dict[str, int]]
    exclusion_counts: dict[str, int]
    event_review_complete: bool


def sample_checksum(samples: list[SplitSample]) -> str:
    return digest([s.model_dump(mode="json") for s in sorted(samples, key=lambda s: s.row_id)])


def _count(rows: list[Membership]) -> dict[str, int]:
    return dict(
        rows=len(rows),
        events=len({r.event_id for r in rows}),
        positive=sum(r.target_value == 1 for r in rows),
        negative=sum(r.target_value == 0 for r in rows),
    )


def _sufficient(rows: list[Membership], events: int, spec: SplitSpec) -> bool:
    counts = _count(rows)
    return (
        counts["events"] >= events
        and counts["positive"] >= spec.min_rows_per_class
        and counts["negative"] >= spec.min_rows_per_class
    )


def _disjoint(sets: list[list[Membership]]) -> None:
    for i, left in enumerate(sets):
        for right in sets[i + 1 :]:
            if {r.event_id for r in left} & {r.event_id for r in right}:
                raise ValueError("Event overlap across partitions")
            if (
                left
                and right
                and max(r.as_of for r in left) + timedelta(hours=24)
                > min(r.as_of for r in right) - timedelta(hours=24)
            ):
                raise ValueError("Feature/label support overlaps a later partition")


def create_splits(
    samples: list[SplitSample],
    catalog: EventCatalog,
    spec: SplitSpec,
    review: EventReview | None = None,
) -> SplitResult:
    spec = SplitSpec.model_validate(spec.model_dump())
    samples = sorted(
        [SplitSample.model_validate(s.model_dump()) for s in samples], key=lambda s: s.row_id
    )
    if len(samples) > 10000 or len({s.row_id for s in samples}) != len(samples):
        raise ValueError("Duplicate or oversized split sample input")
    logical = [(s.target.feature.run_id, s.target.feature.snapshot_id) for s in samples]
    if len(set(logical)) != len(logical):
        raise ValueError("Conflicting feature revisions for one snapshot")
    mapping, groups = reviewed_groups(catalog, review)
    by_key = {r.physical_key: r for r in catalog.observations}
    alias = {
        alias: g.event_id
        for g in catalog.groups
        for key in g.physical_keys
        for alias in by_key[key].revision_hashes
    }
    boundaries = sorted({b.start for b in spec.blocks[1:]} | {b.end for b in spec.blocks[:-1]})
    purge = timedelta(hours=spec.purge_hours_each_side)
    membership = []
    base_reasons = {}
    event_complete = True
    for sample in samples:
        f = sample.target.feature
        reasons = list(sample.label.dataset_exclusions) + list(sample.label.unknown_reasons)
        if not on_cadence(f.as_of, TargetSpec()):
            reasons.append("off_six_hour_cadence")
        if sample.label.label == "unknown" or sample.label.target_value is None:
            reasons.append("unknown_target")
        if digest(f.lineage.get("config")) != spec.feature_spec_sha256:
            reasons.append("frozen_feature_config_mismatch")
        refs = f.lineage.get("member_revisions")
        if not isinstance(refs, list):
            raise ValueError("Missing current member revision references")
        revisions = [r.get("revision_hash") for r in refs if isinstance(r, dict)]
        ids = {mapping[alias[r]] for r in revisions if isinstance(r, str) and r in alias}
        event_id = (
            next(iter(ids))
            if len(ids) == 1
            and len(revisions) == len(refs)
            and all(isinstance(r, str) and r in alias for r in revisions)
            else None
        )
        if event_id is None:
            reasons.append("event_membership_unresolved")
            event_complete = False
        elif groups[event_id][1] != "accept":
            reasons.append(
                "event_review_excluded"
                if groups[event_id][1] == "exclude"
                else "event_review_pending"
            )
            if groups[event_id][1] == "pending":
                event_complete = False
        block = next((b for b in spec.blocks if b.start <= f.as_of < b.end), None)
        if block is None:
            reasons.append("outside_split_blocks")
        if any(boundary - purge <= f.as_of <= boundary + purge for boundary in boundaries):
            reasons.append("boundary_purge")
        # Whole-source event periods govern assignment, not just the subset of labeled rows.
        if event_id:
            group = groups[event_id][0]
            touched = [
                b.partition for b in spec.blocks if group.start < b.end and group.end >= b.start
            ]
            if len(touched) > 1:
                reasons.append("cross_boundary_event")
            elif block and touched != [block.partition]:
                reasons.append("event_period_partition_mismatch")
        base_reasons[sample.row_id] = sorted(
            set(
                r
                for r in reasons
                if r
                not in {"boundary_purge", "cross_boundary_event", "event_period_partition_mismatch"}
            )
        )
        membership.append(
            Membership(
                row_id=sample.row_id,
                event_id=event_id,
                as_of=f.as_of,
                partition=block.partition if block and not reasons else None,
                target_value=sample.label.target_value,
                exclusion_reasons=sorted(set(reasons)),
            )
        )
    if catalog.near_misses_truncated:
        event_complete = False
    partitions = [[r for r in membership if r.partition == name] for name in PARTITIONS]
    _disjoint(partitions)
    counts = {name: _count(rows) for name, rows in zip(PARTITIONS, partitions, strict=True)}
    sufficient = [
        _sufficient(rows, spec.min_train_events if name == "train" else spec.min_eval_events, spec)
        for name, rows in zip(PARTITIONS, partitions, strict=True)
    ]
    folds = []
    for i, (boundary, end) in enumerate(
        zip(spec.cv_boundaries, spec.cv_boundaries[1:], strict=False)
    ):
        train, val, excluded = [], [], []
        for row in membership:
            # Final-test rows never become development data, including under sparse-data fallback.
            if base_reasons[row.row_id] or row.as_of >= spec.blocks[-1].start or not row.event_id:
                excluded.append(row.row_id)
                continue
            group = groups[row.event_id][0]
            # A later final-test block may leave a gap after development. Its
            # later CV end must not admit targets/events extending past the
            # original development boundary, including a previously used test.
            development_end = spec.blocks[-2].end - purge
            if row.as_of >= development_end or group.end >= development_end:
                excluded.append(row.row_id)
                continue
            if row.as_of < boundary - purge and group.end < boundary - purge:
                train.append(row)
            elif (
                boundary + purge < row.as_of < end - purge
                and boundary + purge < group.start
                and group.end < end - purge
            ):
                val.append(row)
            else:
                excluded.append(row.row_id)
        _disjoint([train, val])
        folds.append(
            ForwardFold(
                fold_id=f"forward-{i + 1}",
                boundary=boundary,
                end=end,
                train_ids=sorted(r.row_id for r in train),
                validation_ids=sorted(r.row_id for r in val),
                excluded_ids=sorted(excluded),
                sufficient=_sufficient(train, spec.min_train_events, spec)
                and _sufficient(val, spec.min_eval_events, spec),
            )
        )
    cv_ok = sum(f.sufficient for f in folds) >= spec.min_cv_folds and event_complete
    chronological = all(sufficient) and event_complete
    strategy: Literal["chronological", "grouped_temporal_cv", "baseline_only"] = (
        "chronological" if chronological else "grouped_temporal_cv" if cv_ok else "baseline_only"
    )
    ready = chronological or (cv_ok and sufficient[-1])
    return SplitResult(
        dataset_sha256=sample_checksum(samples),
        spec_sha256=spec.sha256,
        catalog_sha256=catalog.sha256,
        review_sha256=digest(review.model_dump(mode="json")) if review else None,
        membership=membership,
        folds=folds,
        strategy=strategy,
        training_ready=ready,
        development_cv_ready=cv_ok,
        final_holdout_retained=bool(partitions[-1]),
        final_holdout_usable=sufficient[-1],
        release_recommendation="await_model_evaluation" if ready else "baseline_only",
        partition_counts=counts,
        exclusion_counts=dict(
            sorted(
                Counter(reason for row in membership for reason in row.exclusion_reasons).items()
            )
        ),
        event_review_complete=event_complete,
    )
