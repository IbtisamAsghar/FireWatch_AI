"""Development-only score-scale thresholds; priorities do not estimate physical severity."""

import math
from typing import Literal, Self

from pydantic import Field, model_validator

from firewatch.schemas.common import DTO, Digest


class PriorityThresholds(DTO):
    schema_version: Literal["priority-thresholds-v1"] = "priority-thresholds-v1"
    method: str
    score_units: str
    medium: float
    high: float
    critical: float
    provisional: bool = True
    basis: Literal["development_fbeta", "prespecified_baseline"]
    fit_row_ids: list[Digest] = Field(default_factory=list, max_length=10000)
    degenerate_bands: bool

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.medium <= self.high <= self.critical:
            raise ValueError("Priority boundaries must be ordered on the scorer's own scale")
        if self.degenerate_bands != (len({self.medium, self.high, self.critical}) < 3):
            raise ValueError("Priority score resolution must be reported honestly")
        if self.fit_row_ids != sorted(set(self.fit_row_ids)):
            raise ValueError("Threshold cohort must contain unique ordered identities")
        if self.basis == "development_fbeta" and not self.fit_row_ids:
            raise ValueError("Development threshold selection needs exact held-out identities")
        return self

    def priority(self, score: float) -> str:
        if not math.isfinite(score):
            raise ValueError("Nonfinite priority score")
        return (
            "CRITICAL"
            if score >= self.critical
            else "HIGH"
            if score >= self.high
            else "MEDIUM"
            if score >= self.medium
            else "LOW"
        )


def choose_thresholds(
    method: str, units: str, row_ids: list[str], labels: list[int], scores: list[float]
) -> PriorityThresholds:
    if not scores or len(row_ids) != len(scores) or len(labels) != len(scores):
        raise ValueError("Threshold selection requires an identical nonempty development cohort")
    if (
        len(set(row_ids)) != len(row_ids)
        or set(labels) != {0, 1}
        or not all(math.isfinite(s) for s in scores)
    ):
        raise ValueError("Threshold selection requires unique rows, both classes and finite scores")
    groups: dict[float, list[int]] = {}
    for score, label in zip(scores, labels, strict=True):
        groups.setdefault(score, []).append(label)
    positives = sum(labels)
    tp, fp = 0, 0
    options = []
    for threshold in sorted(groups, reverse=True):
        tp += sum(groups[threshold])
        fp += len(groups[threshold]) - sum(groups[threshold])
        options.append((threshold, tp, fp, positives - tp))
    cuts = []
    for beta in (2.0, 1.0, 0.5):

        def quality(option: tuple[float, int, int, int], beta: float = beta) -> tuple[float, float]:
            threshold, true, false, missed = option
            denominator = (1 + beta * beta) * true + beta * beta * missed + false
            return ((1 + beta * beta) * true / denominator if denominator else 0.0, threshold)

        cuts.append(max(options, key=quality)[0])
    cuts.sort()
    return PriorityThresholds(
        method=method,
        score_units=units,
        medium=cuts[0],
        high=cuts[1],
        critical=cuts[2],
        basis="development_fbeta",
        fit_row_ids=sorted(row_ids),
        degenerate_bands=len(set(cuts)) < 3,
    )


def baseline_thresholds(method: str) -> PriorityThresholds:
    if method not in {"baseline-v1", "weighted-baseline-v1"}:
        raise ValueError("No prespecified threshold is valid for this model score")
    cuts, units = (
        ([10, 50, 100], "MW") if method == "baseline-v1" else ([35, 55, 75], "index_0_100")
    )
    return PriorityThresholds(
        method=method,
        score_units=units,
        medium=cuts[0],
        high=cuts[1],
        critical=cuts[2],
        basis="prespecified_baseline",
        degenerate_bands=False,
    )
