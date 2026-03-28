from __future__ import annotations

import base64
import secrets
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from server.app.core.config import ServerSettings

AUTH_REALM = 'Basic realm="RTMS"'
EXEMPT_PATHS = {"/healthz"}


def install_basic_auth(app: FastAPI, settings: ServerSettings) -> None:
    if not settings.auth_enabled:
        return

    username = settings.auth_username or ""
    password = settings.auth_password or ""

    @app.middleware("http")
    async def require_basic_auth(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if _is_exempt_path(request.url.path):
            return await call_next(request)
        if _has_valid_basic_auth(request, username=username, password=password):
            return await call_next(request)
        return PlainTextResponse(
            "Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": AUTH_REALM},
        )


def _is_exempt_path(path: str) -> bool:
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


def _has_valid_basic_auth(request: Request, *, username: str, password: str) -> bool:
    header = request.headers.get("Authorization")
    if not header:
        return False
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic" or not token:
        return False
    try:
        decoded = base64.b64decode(token.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    provided_username, separator, provided_password = decoded.partition(":")
    if separator != ":":
        return False
    return secrets.compare_digest(provided_username, username) and secrets.compare_digest(
        provided_password, password
    )
