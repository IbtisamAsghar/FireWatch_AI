"""Read-only sessions and constant-time operator authentication."""

import secrets
from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from firewatch.config import Settings
from firewatch.db.session import get_session

bearer = HTTPBearer(auto_error=False, scheme_name="OperatorBearer")


def api_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def require_operator(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> None:
    expected = api_settings(request).admin_api_token.get_secret_value().encode()
    supplied = credentials.credentials.encode() if credentials is not None else b""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})


def public_read_policy(
    request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> None:
    if not api_settings(request).public_reads_enabled:
        require_operator(request, credentials)


def get_read_session(
    session: Annotated[Session, Depends(get_session)],
) -> Generator[Session, None, None]:
    session.execute(text("SET TRANSACTION READ ONLY"))
    yield session


ReadSession = Annotated[Session, Depends(get_read_session)]
