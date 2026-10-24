"""History batching and queue-progress health against disposable PostgreSQL."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event
from test_schema import T, outputs, running

from firewatch.config import Settings
from firewatch.db.models import Job
from firewatch.repositories.reads import page_run_times, run_times
from firewatch.runtime import queue_progress
from firewatch.schemas.common import JobStatus, JobType

pytestmark = pytest.mark.integration


def test_history_timestamps_use_two_queries_and_match_single_run_results(session):
    first = running(session)
    outputs(session, first)
    second = running(session)
    _, score = outputs(session, second)
    score.scorer_version = "weighted-baseline-v1"
    session.flush()
    expected = {run.id: run_times(session, run) for run in (first, second)}
    assert expected[second.id][1] is None  # Exclude another scorer's output.
    queries = []
    connection = session.connection()

    def capture(conn, cursor, statement, parameters, context, executemany):
        queries.append(statement)

    event.listen(connection, "before_cursor_execute", capture)
    try:
        assert page_run_times(session, [first, second]) == expected
        assert len(queries) == 2
        assert page_run_times(session, []) == {}
        assert len(queries) == 2
    finally:
        event.remove(connection, "before_cursor_execute", capture)


@pytest.mark.parametrize(
    "status,available,heartbeat,lease,kind,healthy",
    [
        ("queued", -601, None, None, "refresh", False),
        ("queued", -10, None, None, "refresh", True),
        ("queued", 1000, None, None, "replay", True),
        ("running", -1000, -601, 30, "replay", False),
        ("running", -1000, None, None, "refresh", False),
        ("running", -1000, -10, 30, "refresh", True),
        ("running", -1000, -10, -601, "refresh", False),
        ("failed", -1000, None, None, "refresh", True),
        ("queued", -1000, None, None, "anchor", True),
    ],
)
def test_worker_health_detects_stalled_work_but_allows_idle_and_future_jobs(
    session, status, available, heartbeat, lease, kind, healthy
):
    session.add(
        Job(
            type=JobType(kind),
            status=JobStatus(status),
            payload={},
            payload_hash="a" * 64,
            idempotency_key=uuid4().hex,
            available_at=T + timedelta(seconds=available),
            heartbeat_at=T + timedelta(seconds=heartbeat) if heartbeat is not None else None,
            lease_until=T + timedelta(seconds=lease) if lease is not None else None,
        )
    )
    session.flush()
    settings = Settings(
        database_url="postgresql+psycopg://test:password@127.0.0.1:1/firewatch_test",
        admin_api_token="test-operator-" + "x" * 40,
        worker_health_max_lag_seconds=600,
        _env_file=None,
    )
    assert queue_progress(session, settings, T) is healthy
