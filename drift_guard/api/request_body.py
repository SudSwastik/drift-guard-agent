"""Bounded streaming JSON parsing before Pydantic or contract validation."""

import json
import math
from typing import Any

from fastapi import Request
from pydantic import ValidationError

from drift_guard.api.errors import ServiceError
from drift_guard.config import Settings
from drift_guard.validation.models import ValidationRequest


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _number(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("Non-finite JSON number")
    return value


def _constant(text: str) -> None:
    raise ValueError("Invalid JSON constant")


def _check_depth(body: bytes | bytearray, limit: int) -> None:
    depth = 0
    in_string = escaped = False
    for character in body:
        if in_string:
            if escaped:
                escaped = False
            elif character == 92:  # backslash
                escaped = True
            elif character == 34:  # quote
                in_string = False
        elif character == 34:
            in_string = True
        elif character in (91, 123):  # [ or {
            depth += 1
            if depth > limit:
                raise ServiceError(
                    413, "json_too_deep", "JSON nesting exceeds the configured limit."
                )
        elif character in (93, 125):
            depth -= 1


async def read_validation_request(request: Request, settings: Settings) -> ValidationRequest:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise ServiceError(415, "unsupported_media_type", "Content-Type must be application/json.")
    if request.headers.get("content-encoding", "identity").lower() != "identity":
        raise ServiceError(
            415, "unsupported_encoding", "Compressed request bodies are unsupported."
        )
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            length = int(declared_length)
            if length < 0:
                raise ValueError
        except ValueError:
            raise ServiceError(
                400, "invalid_content_length", "Invalid Content-Length header."
            ) from None
        if length > settings.max_request_bytes:
            raise ServiceError(
                413, "request_too_large", "Request body exceeds the configured limit."
            )
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > settings.max_request_bytes:
            raise ServiceError(
                413, "request_too_large", "Request body exceeds the configured limit."
            )
        body.extend(chunk)
    _check_depth(body, settings.max_json_depth)
    try:
        raw = json.loads(
            body.decode("utf-8"),
            object_pairs_hook=_object,
            parse_float=_number,
            parse_constant=_constant,
        )
    except (ValueError, RecursionError):
        raise ServiceError(
            400, "malformed_json", "Request body must be valid UTF-8 JSON."
        ) from None
    try:
        return ValidationRequest.model_validate(raw)
    except ValidationError:
        raise ServiceError(
            422, "invalid_request", "Request does not match the endpoint schema."
        ) from None
