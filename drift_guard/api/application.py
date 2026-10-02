"""Assemble the HTTP application from its components."""

from fastapi import FastAPI

from drift_guard import __version__
from drift_guard.api.errors import register_error_handlers
from drift_guard.api.health import router as health_router
from drift_guard.api.middleware import request_context
from drift_guard.config import Settings, get_settings
from drift_guard.observability import configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create an application with injectable, validated settings."""
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
    register_error_handlers(app)
    app.middleware("http")(request_context)
    app.include_router(health_router)
    return app
