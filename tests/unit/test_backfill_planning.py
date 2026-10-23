"""Frozen seasonal cutoff planning and settings-free backfill CLI help."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from firewatch.cli import app
from firewatch.ml.backfill import cutoffs
from firewatch.ml.dataset import load_design


def test_backfill_plans_only_frozen_six_hour_cutoffs():
    design = load_design(Path("configs/target-dataset-v1.yaml"))
    times = cutoffs(design, None, None)
    assert len(times) == 1472 and len(set(times)) == len(times)
    assert all(t.hour in (0, 6, 12, 18) for t in times)
    assert all(t.year in (2022, 2024) for t in times)
    assert cutoffs(design, times[0], times[1]) == [times[0]]


@pytest.mark.parametrize(
    "start,end",
    [
        ("2021-01-01", "2022-06-01"),
        ("2023-01-01", "2023-02-01"),
        ("2024-05-02", "2024-05-01"),
    ],
)
def test_invalid_or_empty_ranges_are_rejected(start, end):
    design = load_design(Path("configs/target-dataset-v1.yaml"))
    with pytest.raises(ValueError):
        cutoffs(
            design,
            datetime.fromisoformat(start).replace(tzinfo=UTC),
            datetime.fromisoformat(end).replace(tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "command",
    [
        "backfill-training-data",
        "verify-backfill-chunk",
        "merge-backfill-labels",
        "fill-backfill-weather",
    ],
)
def test_backfill_help_requires_no_settings(command, monkeypatch):
    import firewatch.cli

    def denied():
        raise AssertionError("Help accessed settings")

    monkeypatch.setattr(firewatch.cli, "get_settings", denied)
    result = CliRunner().invoke(app, [command, "--help"])
    assert result.exit_code == 0, result.output
