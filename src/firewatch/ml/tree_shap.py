"""Exact path-dependent Shapley values for bounded portable binary forests.

For each leaf, merge repeated splits on a feature. Its coalition value is the
product of known path indicators and unknown weighted-cover fractions. Integrate
its multilinear marginal over t in [0,1]. Gauss-Legendre quadrature is exact for
this polynomial of degree at most depth-1 (no sampling or feature-importance proxy).
"""

import math
from collections import defaultdict
from typing import Any

import numpy as np

from firewatch.ml.forest_artifact import PortableForest
from firewatch.processing.features import MODEL_INPUT_NAMES


class PathDependentSHAP:
    """Compile leaf paths once; explain in O(leaves * depth**2) per row."""

    def __init__(self, forest: PortableForest):
        model = PortableForest.model_validate(forest.model_dump())
        grouped: dict[int, list[Any]] = defaultdict(list)
        base = 0.0
        for tree in model.trees:
            # Bounds are exclusive below and inclusive above, matching sklearn <=.
            pending: list[tuple[int, dict[int, tuple[float, float, float]]]] = [(0, {})]
            while pending:
                index, path = pending.pop()
                node = tree.nodes[index]
                if node.left == -1:
                    keys = sorted(path)
                    fractions = [path[k][0] for k in keys]
                    value = node.class_values[1] / len(model.trees)
                    base += value * math.prod(fractions)
                    if keys:
                        grouped[len(keys)].append(
                            (
                                keys,
                                fractions,
                                [path[k][1] for k in keys],
                                [path[k][2] for k in keys],
                                value,
                            )
                        )
                    continue
                left, right = tree.nodes[node.left], tree.nodes[node.right]
                if not math.isclose(
                    left.weighted_samples + right.weighted_samples,
                    node.weighted_samples,
                    rel_tol=1e-9,
                    abs_tol=1e-9,
                ):
                    raise ValueError("Inconsistent tree covers cannot support path-dependent SHAP")
                for child, goes_left in ((node.left, True), (node.right, False)):
                    child_node = tree.nodes[child]
                    fraction = child_node.weighted_samples / node.weighted_samples
                    if not 0 < fraction <= 1 + 1e-9:
                        raise ValueError("Invalid conditional path cover")
                    zero, lower, upper = path.get(node.feature, (1.0, -math.inf, math.inf))
                    updated = dict(path)
                    updated[node.feature] = (
                        zero * min(1.0, fraction),
                        lower if goes_left else max(lower, node.threshold),
                        min(upper, node.threshold) if goes_left else upper,
                    )
                    pending.append((child, updated))
        self.base_value = base
        self.groups = []
        for depth, leaves in sorted(grouped.items()):
            nodes, weights = np.polynomial.legendre.leggauss((depth + 1) // 2)
            self.groups.append(
                (
                    np.asarray([r[0] for r in leaves], dtype=np.intp),
                    np.asarray([r[1] for r in leaves], dtype=np.float64),
                    np.asarray([r[2] for r in leaves], dtype=np.float64),
                    np.asarray([r[3] for r in leaves], dtype=np.float64),
                    np.asarray([r[4] for r in leaves], dtype=np.float64),
                    (nodes + 1) / 2,
                    weights / 2,
                )
            )

    def explain(self, vector: np.ndarray, raw_score: float) -> list[float]:
        if vector.shape != (len(MODEL_INPUT_NAMES),) or not np.all(np.isfinite(vector)):
            raise ValueError("SHAP needs the exact finite ordered model vector")
        contributions = np.zeros(len(MODEL_INPUT_NAMES), dtype=np.float64)
        for indices, zero, lower, upper, values, points, weights in self.groups:
            observed = vector[indices].astype(np.float64)
            one = ((observed > lower) & (observed <= upper)).astype(np.float64)
            delta = one - zero
            integrated = np.zeros_like(zero)
            for t, weight in zip(points, weights, strict=True):
                factor = zero + t * delta
                prefix = np.ones_like(factor)
                suffix = np.ones_like(factor)
                if factor.shape[1] > 1:
                    prefix[:, 1:] = np.cumprod(factor[:, :-1], axis=1)
                    suffix[:, :-1] = np.cumprod(factor[:, :0:-1], axis=1)[:, ::-1]
                integrated += weight * prefix * suffix
            np.add.at(
                contributions, indices.ravel(), (values[:, None] * delta * integrated).ravel()
            )
        if not np.all(np.isfinite(contributions)) or not math.isclose(
            self.base_value + float(contributions.sum()), raw_score, rel_tol=1e-9, abs_tol=1e-10
        ):
            raise ValueError("SHAP additivity failed; inference withheld")
        return [float(v) for v in contributions]
