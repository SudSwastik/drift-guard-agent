"""Consistent error responses that omit raw inputs and internal exception details."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)},
            "requestId": request.state.request_id,
        },
        headers=exc.headers,
    )


async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "Request does not match the endpoint schema.",
                "fields": [
                    {"path": list(error["loc"]), "code": error["type"]} for error in exc.errors()
                ],
            },
            "requestId": request.state.request_id,
        },
    )


def internal_error(request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {"code": "internal_error", "message": "Internal server error."},
            "requestId": request_id,
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.exception_handler(HTTPException)(http_error)
    app.exception_handler(RequestValidationError)(invalid_request)
