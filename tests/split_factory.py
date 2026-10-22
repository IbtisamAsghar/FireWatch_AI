"""Explicitly synthetic partition fixtures; labels are not real fire evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from feature_factory import inputs, observation

from firewatch.ml.events import EventDecision, EventObservation, EventReview, build_event_catalog
from firewatch.ml.labels import PRIMARY, LabelResult, TargetInput, TargetSpec
from firewatch.ml.splits import SplitSample
from firewatch.processing.features import build_features
from firewatch.sources.firms_normalize import digest


def sample(i, cutoff, *, positive=False, latitude=0.0, longitude=0.0):
    obs = observation(i, acquired=cutoff, latitude=latitude, longitude=longitude)
    args = inputs([obs], cutoff=cutoff)
    feature = build_features(*args)[0]
    target = TargetInput(
        region_id=uuid4(),
        region_name="northern-california",
        products=PRIMARY,
        detail=args[0].snapshots[0],
        feature=feature,
    )
    label = LabelResult(
        target_spec_sha256=TargetSpec().sha256,
        run_id=feature.run_id,
        snapshot_id=feature.snapshot_id,
        as_of=cutoff,
        feature_hash=feature.feature_hash,
        followup_sha256=digest(dict(synthetic=i)),
        baseline_products=PRIMARY,
        label="positive" if positive else "negative",
        target_value=int(positive),
        threshold_mw=15.0,
        unobserved_followup=False,
        unknown_reasons=[],
        dataset_exclusions=[],
        later_detection_count=1,
        valid_later_frp_count=1,
        max_later_frp_mw=20 if positive else 10,
        stronger_detection_ids=[],
        acquisition_hours_utc={"12": 1},
    )
    event = EventObservation(
        physical_key=obs.physical_key,
        acquired_at=cutoff,
        latitude=latitude,
        longitude=longitude,
        revision_hashes=[obs.revision_hash],
    )
    return SplitSample(target=target, label=label), event


def reviewed(catalog):
    return EventReview(
        catalog_sha256=catalog.sha256,
        reviewer="synthetic-test-review",
        decisions={
            g.event_id: EventDecision(decision="accept", reason="Synthetic isolated event")
            for g in catalog.groups
        },
        merge_sets=[],
        note="Synthetic reviewed partition identities, not real events",
    )


def cohort(*, cv=False, holdout=True, all_positive=False):
    dates = (
        [
            "2022-05-05",
            "2022-05-15",
            "2022-05-25",
            "2022-07-05",
            "2022-07-15",
            "2022-09-05",
            "2022-09-15",
            "2024-05-10",
            "2024-05-20",
            "2024-07-20",
            "2024-07-30",
        ]
        if cv
        else [
            "2022-05-05",
            "2022-05-15",
            "2022-05-25",
            "2024-05-10",
            "2024-05-20",
            "2024-06-20",
            "2024-06-30",
            "2024-07-20",
            "2024-07-30",
        ]
    )
    if holdout:
        dates += ["2024-08-20", "2024-08-30"]
    samples, observations = [], []
    for group, day in enumerate(dates):
        start = datetime.fromisoformat(day).replace(tzinfo=UTC)
        for j in range(6):
            row, obs = sample(
                group * 10 + j + 1,
                start + timedelta(hours=j * 6),
                positive=all_positive or j % 2 == 0,
            )
            samples.append(row)
            observations.append(obs)
    catalog = build_event_catalog(observations, "a" * 64)
    return samples, catalog, reviewed(catalog)
