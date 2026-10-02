"""Health and request-context checks for the service foundation."""

from collections.abc import Mapping

import anyio
import pytest
from fastapi import FastAPI
from httpx import Response
from pydantic import BaseModel, SecretStr

from drift_guard.api.application import create_app
from drift_guard.config import Settings
from drift_guard.storage.repository import SQLObservationRepository
from tests.client import managed_client


def request(app: FastAPI, path: str, headers: Mapping[str, str] | None = None) -> Response:
    """Send an in-process request without starting a server or deprecated test client."""

    async def send() -> Response:
        async with managed_client(app) as client:
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
    app = create_app(
        Settings(
            environment="production",
            api_key=SecretStr("test-only-api-key-32-characters-long"),
            database_url=SecretStr("sqlite+aiosqlite:///:memory:"),
        ),
        observation_repository=SQLObservationRepository("sqlite+aiosqlite:///:memory:"),
    )

    assert request(app, "/docs").status_code == 404
    assert request(app, "/openapi.json").status_code == 404


def test_unknown_route_returns_structured_error_with_request_id() -> None:
    response = request(create_app(Settings(environment="test")), "/missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"
    assert response.json()["requestId"] == response.headers["x-request-id"]


def test_unexpected_error_does_not_expose_exception_details() -> None:
    app = create_app(Settings(environment="test"))

    @app.get("/test-failure")
    async def failure() -> None:
        raise RuntimeError("sensitive internal detail")

    response = request(app, "/test-failure")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "sensitive internal detail" not in response.text
    assert response.json()["requestId"] == response.headers["x-request-id"]


class ExampleRequest(BaseModel):
    amount: int


@pytest.mark.parametrize("content", ['{"amount":"sensitive-value"}', '{"amount":"sensitive-value"'])
def test_invalid_request_error_excludes_submitted_values(content: str) -> None:
    app = create_app(Settings(environment="test"))

    @app.post("/test-request")
    async def example(payload: ExampleRequest) -> ExampleRequest:
        return payload

    async def send() -> Response:
        async with managed_client(app) as client:
            return await client.post(
                "/test-request", content=content, headers={"Content-Type": "application/json"}
            )

    response = anyio.run(send)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "sensitive-value" not in response.text
    assert response.json()["requestId"] == response.headers["x-request-id"]
