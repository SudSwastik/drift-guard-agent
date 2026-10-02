"""Consistent error responses that omit raw inputs and internal exception details."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class ServiceError(Exception):
    """A public error with a stable code and an input-independent message."""

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {"code": exc.code, "message": exc.message},
            "requestId": request.state.request_id,
        },
        headers={"Retry-After": "1"} if exc.status == 429 else None,
    )


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
    app.exception_handler(ServiceError)(service_error)
    app.exception_handler(HTTPException)(http_error)
    app.exception_handler(RequestValidationError)(invalid_request)
