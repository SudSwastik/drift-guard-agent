"""Assemble the HTTP application from its components."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI

from drift_guard import __version__
from drift_guard.api.contracts import router as contracts_router
from drift_guard.api.drift import router as drift_router
from drift_guard.api.errors import register_error_handlers
from drift_guard.api.health import router as health_router
from drift_guard.api.middleware import request_context
from drift_guard.api.validation import router as validation_router
from drift_guard.config import Settings, get_settings
from drift_guard.contracts.repository import (
    BUNDLED_CONTRACTS,
    ContractRepository,
    FileContractRepository,
)
from drift_guard.observability import configure_logging
from drift_guard.storage.bootstrap import observation_service
from drift_guard.storage.repository import ObservationRepository


def create_app(
    settings: Settings | None = None,
    contract_repository: ContractRepository | None = None,
    observation_repository: ObservationRepository | None = None,
) -> FastAPI:
    """Create an application with injectable, validated settings."""
    service_settings = settings or get_settings()
    configure_logging(service_settings.log_level)
    repository = (
        contract_repository
        if contract_repository is not None
        else FileContractRepository(service_settings.contracts_directory or BUNDLED_CONTRACTS)
    )
    observations = observation_service(service_settings, observation_repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            await observations.start()
            async with anyio.create_task_group() as group:
                group.start_soon(observations.retention_loop)
                try:
                    yield
                finally:
                    group.cancel_scope.cancel()
        finally:
            await observations.repository.close()

    app = FastAPI(
        lifespan=lifespan,
        title="Drift Guard Agent",
        version=__version__,
        openapi_url="/openapi.json" if service_settings.environment != "production" else None,
        docs_url="/docs" if service_settings.environment != "production" else None,
        redoc_url=None,
    )
    app.state.settings = service_settings
    app.state.contract_repository = repository
    app.state.observation_service = observations
    app.state.validation_slots = anyio.CapacityLimiter(service_settings.validation_concurrency)
    app.state.validation_workers = anyio.CapacityLimiter(service_settings.validation_concurrency)
    register_error_handlers(app)
    app.middleware("http")(request_context)
    app.include_router(health_router)
    app.include_router(contracts_router)
    app.include_router(validation_router)
    app.include_router(drift_router)
    return app
