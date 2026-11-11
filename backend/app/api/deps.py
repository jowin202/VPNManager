from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..context import AppContext

_bearer = HTTPBearer(auto_error=False)


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def require_auth(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    settings = request.app.state.ctx.settings
    if settings.auth_disabled:
        return
    if not creds or not secrets.compare_digest(creds.credentials.encode(), settings.api_token.encode()):
        raise HTTPException(401, "Invalid or missing API token.", headers={"WWW-Authenticate": "Bearer"})
