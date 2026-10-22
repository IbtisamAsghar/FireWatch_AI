"""Synthetic classifier cohorts, not real observed-FRP performance evidence."""

from datetime import UTC, datetime, timedelta

from split_factory import cohort, reviewed, sample

from firewatch.ml.events import build_event_catalog
from firewatch.ml.forest_spec import ForestSpec
from firewatch.ml.forest_training import ForestData
from firewatch.ml.splits import create_splits, default_split_spec
from firewatch.sources.firms_normalize import digest


def small_spec(**overrides):
    return ForestSpec(tree_counts=[8], depths=[3], leaf_sizes=[1], **overrides)


def forest_data(*, calibration=None, cv=False, holdout=True, all_positive=False):
    if calibration is None:
        rows, catalog, review = cohort(cv=cv, holdout=holdout, all_positive=all_positive)
    else:
        days = [
            "2022-05-05",
            "2022-05-15",
            "2022-05-25",
            "2024-05-10",
            "2024-05-20",
            "2024-06-17",
            "2024-06-20",
            "2024-06-23",
            "2024-06-26",
            "2024-07-03",
            "2024-07-06",
            "2024-07-09",
            "2024-07-12",
            "2024-07-20",
            "2024-07-30",
            "2024-08-20",
            "2024-08-30",
        ]
        rows, observations = [], []
        for group, day in enumerate(days):
            start = datetime.fromisoformat(day).replace(tzinfo=UTC)
            for j in range(6):
                positive = j % 2 == 0
                noisy = (5 <= group <= 8) or (9 <= group <= 12 and calibration == "accepted")
                if noisy and j == (0 if group % 2 == 0 else 1):
                    positive = not positive
                row, obs = sample(
                    group * 10 + j + 1,
                    start + timedelta(hours=j * 6),
                    positive=positive,
                    latitude=group * 0.2,
                )
                # The synthetic signal follows j, with independently perturbed cal labels.
                row.target.feature.values["frp_max_mw"] = 100.0 if j % 2 == 0 else 1.0
                rows.append(row)
                observations.append(obs)
        catalog = build_event_catalog(observations, "a" * 64)
        review = reviewed(catalog)
    for row in rows:
        if calibration is None:
            row.target.feature.values["frp_max_mw"] = 100.0 if row.label.target_value else 1.0
        row.target.feature.values["frp_sum_mw"] = row.target.feature.values["frp_max_mw"]
        row.target.feature.feature_hash = digest(
            row.target.feature.model_dump(mode="json", exclude={"feature_hash"})
        )
        row.label.feature_hash = row.target.feature.feature_hash
    split_spec = default_split_spec()
    result = create_splits(rows, catalog, split_spec, review)
    return ForestData(rows, result, split_spec, catalog, review)
