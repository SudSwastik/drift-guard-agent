"""Replaceable authentication dependency; secrets are never included in responses."""

import secrets
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException

from drift_guard.config import Settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    request: Request, supplied: Annotated[str | None, Depends(api_key_header)]
) -> None:
    settings: Settings = request.app.state.settings
    if settings.api_key is None:
        return  # Only local/test settings allow unauthenticated development.
    expected = settings.api_key.get_secret_value().encode("utf-8")
    if supplied is None or not secrets.compare_digest(supplied.encode("utf-8"), expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
