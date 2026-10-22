"""Deterministic standalone reliability figures and a readable limitations-first model card."""

from pathlib import Path
from typing import Any

import matplotlib
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


def write_model_card(root: Path, report: dict[str, Any]) -> None:
    final = bool(report.get("final_test_evaluated"))
    cohort = report.get("final") if final else report.get("development")
    lines = [
        "# FireWatch candidate model card",
        "",
        f"Evaluation state: {report.get('status', 'release_evaluated')}.",
        "",
        f"Final test: {report['final_test_status']}.",
        (
            "No model was promoted. Research decision support only; "
            "not an emergency-response system."
        ),
        "",
        "## Intended target and limits",
        "",
        "The label is later satellite-observed FRP strengthening, not physical wildfire severity.",
        (
            "Event IDs are thermal-activity partition proxies. "
            "Repeated snapshots are not independent fires."
        ),
        (
            "Overpass, cloud/coverage, unobserved negatives, sensor/product "
            "and weather gaps limit generalization."
        ),
        "",
        "## Results",
        "",
    ]
    if not cohort:
        lines += [
            "No supported held-out metrics or fitted-model performance can be reported.",
            "The available cohort is insufficient; retain the baseline-only MVP.",
        ]
    else:
        lines.append(
            "Development threshold-fit metrics are optimistic selection diagnostics."
            if not final
            else (
                "Frozen methods and development-selected thresholds "
                "were evaluated on the single release holdout."
            )
        )
        for name, item in sorted(cohort["methods"].items()):
            value = item["metrics"]

            def number(x: Any) -> str:
                return "undefined" if x is None else f"{x:.6g}"

            lines.append(
                f"- {name}: n={value['rows']}, events={value['events']}, "
                f"precision={number(value['precision'])}, recall={number(value['recall'])}, "
                f"F1={number(value['f1'])}, AP={number(value['average_precision'])}, "
                f"trapezoidal PR-AUC={number(value['pr_auc'])}."
            )
            if value["probability"] is not None:
                lines.append(
                    f"  Calibrated Brier={number(value['probability']['brier'])}; "
                    f"[reliability plot](reliability-{name}.svg)."
                )
        lines += [
            "",
            (
                "Whole-event uncertainty requires sufficient events; "
                "absent intervals do not mean zero uncertainty."
            ),
        ]
    lines += [
        "",
        "## Priority policy",
        "",
        (
            "Low/Medium/High/Critical are score-scale research categories, "
            "not validated physical severity."
        ),
        (
            "Development F2/F1/F0.5 objectives select ordered boundaries. "
            "Ties can collapse bands; thresholds remain provisional."
        ),
        (
            "Forest fractions and logistic margins are model_score. "
            "Only an accepted sigmoid can supply a nullable probability."
        ),
        "",
        "## Failures and review requirements",
        "",
    ]
    lines.extend("- " + item for item in report.get("failures", []))
    lines += [
        "",
        f"Release recommendation: {report.get('release_recommendation', 'baseline_only')}.",
        (
            "Independent limitations/model-card review and registry approval "
            "are required before ML promotion."
        ),
        "",
    ]
    (root / "model-card.md").write_text("\n".join(lines), encoding="utf-8")
    if not cohort:
        return
    for name, item in cohort["methods"].items():
        probability = item["metrics"]["probability"]
        if probability is None:
            continue
        if name != "forest":
            raise ValueError("Unsupported probability plot method")
        bins = [b for b in probability["reliability"] if b["rows"]]
        with matplotlib.rc_context(
            {"svg.hashsalt": "firewatch-reliability-v1", "font.family": "DejaVu Sans"}
        ):
            figure = Figure(figsize=(5, 4), layout="constrained")
            FigureCanvasAgg(figure)
            axis = figure.subplots()
            axis.plot([0, 1], [0, 1], linestyle="--", color="gray", label="Ideal reference")
            axis.plot(
                [b["mean_probability"] for b in bins],
                [b["observed_fraction"] for b in bins],
                marker="o",
                label="Observed cohort",
            )
            axis.set(
                xlim=(0, 1),
                ylim=(0, 1),
                xlabel="Mean calibrated probability",
                ylabel="Observed positive fraction",
                title="Forest reliability: " + ("final holdout" if final else "development"),
            )
            axis.legend(loc="upper left")
            figure.savefig(
                root / f"reliability-{name}.svg",
                format="svg",
                metadata={"Date": None, "Creator": "FireWatch evaluation-v1"},
            )
            figure.clear()
