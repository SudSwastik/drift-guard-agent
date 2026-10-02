"""Liveness and readiness routes."""

from fastapi import APIRouter, Request

from drift_guard import __version__
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
    return {"status": "ready", "service": settings.service_name, "version": __version__}
