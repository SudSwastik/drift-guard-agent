"""Liveness and readiness routes."""

from fastapi import APIRouter, Request

from drift_guard import __version__
from drift_guard.api.errors import ServiceError
from drift_guard.config import Settings

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def liveness() -> dict[str, str]:
    """Indicate the process can respond independently of downstream services."""
    return {"status": "ok"}


@router.get("/ready")
async def readiness(request: Request) -> dict[str, str]:
    """Indicate the service initialized and is ready to receive requests."""
    settings: Settings = request.app.state.settings
    if settings.observation_failure_mode == "required":
        if not await request.app.state.observation_service.health():
            raise ServiceError(
                503,
                "observation_storage_unavailable",
                "Required observation storage is unavailable.",
            )
    return {"status": "ready", "service": settings.service_name, "version": __version__}
