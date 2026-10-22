"""Frozen evaluation, priority-threshold and release-review protocol."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field

from firewatch.schemas.common import DTO
from firewatch.sources.firms_normalize import digest


class EvaluationSpec(DTO):
    schema_version: Literal["release-evaluation-v1"] = "release-evaluation-v1"
    bootstrap_repetitions: int = Field(default=200, ge=20, le=500)
    bootstrap_seed: Literal[42] = 42
    min_bootstrap_events: int = Field(default=5, ge=5, le=100)
    min_threshold_events: int = Field(default=2, ge=2, le=100)
    min_threshold_rows_per_class: int = Field(default=5, ge=5, le=1000)
    min_final_events: int = Field(default=5, ge=5, le=100)
    min_final_rows_per_class: int = Field(default=10, ge=10, le=1000)
    threshold_objectives: Literal["F2_F1_F0.5"] = "F2_F1_F0.5"
    confidence_level: Literal[95] = 95
    promotion_gate: Literal["paired_event_bootstrap_ap_improvement_over_both_baselines"] = (
        "paired_event_bootstrap_ap_improvement_over_both_baselines"
    )

    @property
    def sha256(self) -> str:
        return digest(self.model_dump(mode="json"))


def load_evaluation_spec(path: Path) -> EvaluationSpec:
    with path.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Oversized evaluation policy")
    return EvaluationSpec.model_validate(yaml.safe_load(raw))
