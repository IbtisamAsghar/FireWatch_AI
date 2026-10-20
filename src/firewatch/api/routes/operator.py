"""Authenticated, bounded and idempotent durable job submission."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.orm import Session

from firewatch.api.dependencies import api_settings, require_operator
from firewatch.api.errors import APIError
from firewatch.db.session import get_session
from firewatch.repositories.jobs import anchor_payload, enqueue, refresh_payload, replay_payload
from firewatch.schemas.api import ErrorEnvelope
from firewatch.schemas.common import JobType
from firewatch.schemas.operator import AnchorRequest, JobAccepted, RefreshRequest, ReplayRequest

RESPONSES: dict[int | str, dict[str, Any]] = {
    code: {"model": ErrorEnvelope} for code in (400, 401, 404, 408, 409, 413, 415, 422, 503)
}
router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_operator)], responses=RESPONSES)
WriteSession = Annotated[Session, Depends(get_session)]
IdempotencyKey = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]


def submit(
    request: Request,
    response: Response,
    session: Session,
    kind: JobType,
    key: str,
    identity: dict[str, Any],
    body: RefreshRequest | ReplayRequest | UUID,
) -> JobAccepted:
    if len(request.headers.getlist("idempotency-key")) != 1:
        raise APIError(
            422, "INVALID_IDEMPOTENCY_KEY", "Exactly one Idempotency-Key header is required."
        )
    settings = api_settings(request)
    now = request.app.state.clock()

    def payload() -> dict[str, Any]:
        if isinstance(body, RefreshRequest):
            return refresh_payload(session, settings, body, now)
        if isinstance(body, ReplayRequest):
            return replay_payload(session, settings, body, now)
        return anchor_payload(session, settings, body, now)

    job, reused = enqueue(session, settings, kind, key, identity, now, payload)
    response.headers["Location"] = f"/api/v1/jobs/{job.id}"
    response.headers["Idempotency-Replayed"] = str(reused).lower()
    return JobAccepted(
        job_id=job.id,
        type=job.type,
        status=job.status,
        available_at=job.available_at,
        result_run_id=job.result_run_id,
    )


@router.post(
    "/jobs/refresh", status_code=202, response_model=JobAccepted, operation_id="queue_refresh"
)
def refresh(
    body: RefreshRequest,
    request: Request,
    response: Response,
    session: WriteSession,
    key: IdempotencyKey,
) -> JobAccepted:
    return submit(
        request, response, session, JobType.REFRESH, key, body.model_dump(mode="json"), body
    )


@router.post(
    "/jobs/replay", status_code=202, response_model=JobAccepted, operation_id="queue_replay"
)
def replay(
    body: ReplayRequest,
    request: Request,
    response: Response,
    session: WriteSession,
    key: IdempotencyKey,
) -> JobAccepted:
    return submit(
        request, response, session, JobType.REPLAY, key, body.model_dump(mode="json"), body
    )


@router.post(
    "/alerts/{alert_id}/anchor",
    status_code=202,
    response_model=JobAccepted,
    operation_id="queue_anchor",
)
def anchor(
    alert_id: UUID,
    body: AnchorRequest,
    request: Request,
    response: Response,
    session: WriteSession,
    key: IdempotencyKey,
) -> JobAccepted:
    return submit(
        request, response, session, JobType.ANCHOR, key, {"alert_id": str(alert_id)}, alert_id
    )
