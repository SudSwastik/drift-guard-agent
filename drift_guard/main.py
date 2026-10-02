"""ASGI entry point for Uvicorn."""

from drift_guard.api.application import create_app

app = create_app()
