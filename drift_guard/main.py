"""HTTP application entry point."""

import logging
import re
import time
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from drift_guard import __version__
from drift_guard.config import Settings, get_settings
from drift_guard.observability import configure_logging

logger = logging.getLogger(__name__)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create the application; settings can be injected in tests and local tooling."""
    service_settings = settings or get_settings()
    configure_logging(service_settings.log_level)

    app = FastAPI(
        title="Drift Guard Agent",
        version=__version__,
        openapi_url="/openapi.json" if service_settings.environment != "production" else None,
        docs_url="/docs" if service_settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = service_settings

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        started_at = time.perf_counter()
        supplied_id = request.headers.get("x-request-id", "")
        request_id = supplied_id if _REQUEST_ID_PATTERN.fullmatch(supplied_id) else str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "http_request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started_at) * 1000, 2),
            },
        )
        return response

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        """Indicate the process can respond; do not depend on downstream services."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def readiness() -> dict[str, str]:
        """Indicate the service initialized and is ready to receive requests."""
        return {
            "status": "ready",
            "service": service_settings.service_name,
            "version": __version__,
        }

    return app


app = create_app()
