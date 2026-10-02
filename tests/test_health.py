"""Health and request-context checks for the service foundation."""

from collections.abc import Mapping

import anyio
from httpx import ASGITransport, AsyncClient, Response
from starlette.types import ASGIApp

from drift_guard.config import Settings
from drift_guard.main import create_app


def request(
    app: ASGIApp, path: str, headers: Mapping[str, str] | None = None
) -> Response:
    """Send an in-process request without starting a server or deprecated test client."""

    async def send() -> Response:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get(path, headers=headers)

    return anyio.run(send)


def test_health_endpoints_report_service_readiness() -> None:
    app = create_app(Settings(environment="test", service_name="test-service"))

    live = request(app, "/health/live")
    ready = request(app, "/health/ready")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "service": "test-service",
        "version": "0.1.0",
    }


def test_request_id_is_returned_and_invalid_ids_are_replaced() -> None:
    app = create_app(Settings(environment="test"))

    accepted = request(app, "/health/live", headers={"X-Request-ID": "build-42:check"})
    replaced = request(app, "/health/live", headers={"X-Request-ID": "not safe\nvalue"})

    assert accepted.headers["x-request-id"] == "build-42:check"
    assert replaced.headers["x-request-id"] != "not safe\nvalue"
    assert len(replaced.headers["x-request-id"]) == 36


def test_openapi_docs_are_hidden_in_production() -> None:
    app = create_app(Settings(environment="production"))

    assert request(app, "/docs").status_code == 404
    assert request(app, "/openapi.json").status_code == 404
