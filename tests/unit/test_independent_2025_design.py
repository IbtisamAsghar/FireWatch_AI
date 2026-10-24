from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError
from split_factory import reviewed, sample

from firewatch.ml.dataset import load_design
from firewatch.ml.events import build_event_catalog
from firewatch.ml.labels import DatasetDesign
from firewatch.ml.split_package import load_split_spec, validate_design_split
from firewatch.ml.splits import create_splits


def test_2025_design_is_explicit_and_keeps_original_holdout_outside_development():
    design = load_design(Path("configs/target-dataset-2025-holdout-v1.yaml"))
    split = load_split_spec(Path("configs/splits-2025-holdout-v1.yaml"))
    assert design.periods[-1].start == datetime(2025, 5, 1, tzinfo=UTC)
    assert split.blocks[-1].start == design.periods[-1].start
    old_test_start = datetime(2024, 8, 15, tzinfo=UTC)
    assert all(block.end <= old_test_start for block in split.blocks[:-1])
    assert split.purge_hours_each_side >= 48
    assert split.min_rows_per_class == 5
    validate_design_split(design, split)


def test_new_design_refuses_old_holdout_or_weakened_split_policy():
    design = load_design(Path("configs/target-dataset-2025-holdout-v1.yaml"))
    with pytest.raises(ValueError, match="frozen independent-holdout"):
        validate_design_split(design, load_split_spec(Path("configs/splits-v1.yaml")))
    split = load_split_spec(Path("configs/splits-2025-holdout-v1.yaml"))
    changed = split.model_copy(update={"min_rows_per_class": 1})
    with pytest.raises(ValueError, match="frozen independent-holdout"):
        validate_design_split(design, changed)


def test_original_design_still_refuses_added_season():
    raw = load_design(Path("configs/target-dataset-2025-holdout-v1.yaml")).model_dump()
    raw.update(schema_version="target-dataset-design-v1", split_policy="deferred_to_phase_4_2")
    with pytest.raises(ValidationError, match="season design"):
        DatasetDesign.model_validate(raw)


def test_2025_design_refuses_arbitrary_dates_and_policy_mismatch():
    raw = load_design(Path("configs/target-dataset-2025-holdout-v1.yaml")).model_dump()
    raw["periods"][-1]["start"] = datetime(2025, 6, 1, tzinfo=UTC)
    with pytest.raises(ValidationError, match="season design"):
        DatasetDesign.model_validate(raw)
    raw = load_design(Path("configs/target-dataset-2025-holdout-v1.yaml")).model_dump()
    raw["split_policy"] = "deferred_to_phase_4_2"
    with pytest.raises(ValidationError, match="split policy"):
        DatasetDesign.model_validate(raw)


def test_gapped_final_holdout_never_admits_late_2024_target_support():
    split = load_split_spec(Path("configs/splits-2025-holdout-v1.yaml"))
    row, observation = sample(700, datetime(2024, 8, 14, tzinfo=UTC), positive=True)
    catalog = build_event_catalog([observation], "a" * 64)
    result = create_splits([row], catalog, split, reviewed(catalog))
    assert all(row.row_id not in fold.train_ids + fold.validation_ids for fold in result.folds)


def test_gapped_final_holdout_excludes_events_continuing_into_old_test():
    split = load_split_spec(Path("configs/splits-2025-holdout-v1.yaml"))
    start = datetime(2024, 7, 25, tzinfo=UTC)
    row, observation = sample(800, start, positive=True)
    chain = [observation]
    for index in range(1, 17):
        _, extra = sample(800 + index, start + timedelta(hours=36 * index), positive=True)
        chain.append(extra)
    catalog = build_event_catalog(chain, "a" * 64)
    result = create_splits([row], catalog, split, reviewed(catalog))
    assert all(row.row_id not in fold.train_ids + fold.validation_ids for fold in result.folds)
