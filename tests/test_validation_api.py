"""HTTP validation, request protection, worker deadlines, and privacy checks."""

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest
from fastapi import FastAPI
from httpx import Response
from pydantic import SecretStr

from drift_guard.api import validation as validation_api
from drift_guard.api.application import create_app
from drift_guard.config import Settings
from drift_guard.contracts.repository import FileContractRepository
from tests.client import managed_client
from tests.test_validation import CASES, custom_contract

KEY = "test-only-api-key-32-characters-long"


def envelope(payload: Any = None, **changes: Any) -> dict[str, Any]:
    return {
        "api": "payments",
        "version": "v1",
        "operation": "POST /payments",
        "direction": "request",
        "payload": payload,
        **changes,
    }


def post(app: FastAPI, content: str | bytes, **headers: str) -> Response:
    async def send() -> Response:
        async with managed_client(app) as client:
            return await client.post(
                "/v1/validate",
                content=content,
                headers={"Content-Type": "application/json", **headers},
            )

    return anyio.run(send)


@pytest.mark.parametrize("blocking", [False, True])
def test_golden_cases_through_real_worker_and_authenticated_api(blocking: bool) -> None:
    app = create_app(
        Settings(environment="test", api_key=SecretStr(KEY), blocking_enabled=blocking)
    )

    async def send() -> None:
        async with managed_client(app) as client:
            for case in CASES:
                response = await client.post(
                    "/v1/validate",
                    json=envelope(case["payload"]),
                    headers={"X-API-Key": KEY, "X-Request-ID": "golden-check"},
                )
                assert response.status_code == 200, response.text
                result = response.json()
                assert [[item["code"], item["path"]] for item in result["findings"]] == case[
                    "findings"
                ]
                assert result["severity"] == case["severity"]
                expected = "ALLOW" if not case["findings"] else "WARN"
                if blocking and case["severity"] in ("MEDIUM", "HIGH"):
                    expected = "BLOCK"
                assert result["decision"] == expected
                assert result["mode"] == ("enforce" if blocking else "report_only")
                assert result["requestId"] == response.headers["x-request-id"] == "golden-check"
                assert len(result["contractHash"]) == 64

    anyio.run(send)


@pytest.mark.parametrize("key", [None, "incorrect"])
def test_unauthorized_caller_rejected_before_parsing(key: str | None) -> None:
    app = create_app(Settings(environment="test", api_key=SecretStr(KEY)))
    headers = {"X-API-Key": key} if key is not None else {}
    response = post(app, "not even JSON", **headers)
    assert response.status_code == 401
    assert KEY not in response.text


@pytest.mark.parametrize(
    ("content", "status", "code"),
    [
        ('{"payload":', 400, "malformed_json"),
        ('{"payload":NaN}', 400, "malformed_json"),
        ('{"payload":Infinity}', 400, "malformed_json"),
        ('{"payload":1e400}', 400, "malformed_json"),
        ('{"api":"payments","api":"other"}', 400, "malformed_json"),
        (b"\xff", 400, "malformed_json"),
        ("{}", 422, "invalid_request"),
        ("[]", 422, "invalid_request"),
        (json.dumps(envelope({}, version="v2")), 404, "unknown_contract"),
        (json.dumps(envelope({}, direction="other")), 422, "invalid_request"),
        (json.dumps(envelope({}, context={"unapproved": "private-data"})), 422, "invalid_request"),
    ],
)
def test_request_failures_are_distinct_from_contract_violations(
    content: str | bytes, status: int, code: str
) -> None:
    response = post(create_app(Settings(environment="test")), content)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["requestId"] == response.headers["x-request-id"]
    assert "private-data" not in response.text


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"Content-Type": "text/plain"}, 415, "unsupported_media_type"),
        ({"Content-Encoding": "gzip"}, 415, "unsupported_encoding"),
        ({"Content-Length": "-1"}, 400, "invalid_content_length"),
        ({"Content-Length": "invalid"}, 400, "invalid_content_length"),
        ({"Content-Length": "65537"}, 413, "request_too_large"),
    ],
)
def test_header_limits(headers: dict[str, str], status: int, code: str) -> None:
    response = post(create_app(Settings(environment="test")), "{}", **headers)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_streamed_body_limit_is_enforced_without_content_length() -> None:
    app = create_app(Settings(environment="test", max_request_bytes=256))

    async def chunks() -> AsyncIterator[bytes]:
        yield b" " * 200
        yield b" " * 100

    async def send() -> None:
        async with managed_client(app) as client:
            response = await client.post(
                "/v1/validate",
                content=chunks(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 413
            assert response.json()["error"]["code"] == "request_too_large"

    anyio.run(send)


def test_depth_limits_count_containers_but_ignore_brackets_in_strings() -> None:
    app = create_app(Settings(environment="test", max_json_depth=2))
    response = post(app, json.dumps(envelope({"nested": {"too_deep": 1}})))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "json_too_deep"
    safe = {**CASES[0]["payload"], "customerId": '[{\\"}]'}
    response = post(app, json.dumps(envelope(safe)))
    assert response.status_code == 200
    assert response.json()["decision"] == "ALLOW"


def test_payload_values_context_and_key_are_not_logged(capsys: pytest.CaptureFixture[str]) -> None:
    app = create_app(Settings(environment="test", api_key=SecretStr(KEY)))
    payload = {**CASES[0]["payload"], "amount": "private-amount-value"}
    response = post(
        app,
        json.dumps(envelope(payload, context={"clientId": "private-client-id"})),
        **{"X-API-Key": KEY},
    )
    assert response.status_code == 200
    logs = capsys.readouterr().err
    for secret in (KEY, "private-amount-value", "private-client-id"):
        assert secret not in logs and secret not in response.text
    record = next(json.loads(line) for line in logs.splitlines() if '"payload_validated"' in line)
    assert record["contract_hash"] == response.json()["contractHash"]
    assert record["decision"] == "WARN" and record["finding_count"] == 1
    assert record["severity"] == "INFO" and record["validation_severity"] == "HIGH"


def test_worker_error_is_service_failure_and_excludes_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("private-dependency-detail")

    monkeypatch.setattr(validation_api.to_process, "run_sync", unavailable)
    response = post(create_app(Settings(environment="test")), json.dumps(envelope({})))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "validation_unavailable"
    assert "private-dependency-detail" not in response.text


def test_slow_body_times_out_and_releases_capacity() -> None:
    app = create_app(
        Settings(environment="test", validation_timeout_seconds=0.1, validation_concurrency=1)
    )

    async def slow_body() -> AsyncIterator[bytes]:
        await anyio.sleep(1)
        yield b"{}"

    async def send() -> None:
        async with managed_client(app) as client:
            response = await client.post(
                "/v1/validate",
                content=slow_body(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 504
            assert response.json()["error"]["code"] == "validation_timeout"
            assert app.state.validation_slots.borrowed_tokens == 0

    anyio.run(send)


def test_busy_capacity_returns_429_and_recovers() -> None:
    app = create_app(Settings(environment="test", validation_concurrency=1))

    async def send() -> None:
        slots = app.state.validation_slots
        slots.acquire_nowait()
        try:
            async with managed_client(app) as client:
                response = await client.post("/v1/validate", json=envelope({}))
                assert response.status_code == 429
                assert response.headers["retry-after"] == "1"
        finally:
            slots.release()
        assert slots.borrowed_tokens == 0

    anyio.run(send)


def test_cpu_timeout_kills_worker_and_next_request_recovers(tmp_path: Path) -> None:
    custom_contract(tmp_path, {"type": "string", "pattern": "^(a+)+$"})
    app = create_app(
        Settings(environment="test", validation_timeout_seconds=1),
        contract_repository=FileContractRepository(tmp_path),
    )

    async def send() -> None:
        async with managed_client(app) as client:
            data = envelope("a", api="example", operation="POST /example")
            assert (await client.post("/v1/validate", json=data)).status_code == 200
            data["payload"] = "a" * 32 + "!"
            started = time.monotonic()
            response = await client.post("/v1/validate", json=data)
            assert response.status_code == 504
            assert response.json()["error"]["code"] == "validation_timeout"
            assert time.monotonic() - started < 4
            data["payload"] = "a"
            response = await client.post("/v1/validate", json=data)
            assert response.status_code == 200
            assert response.json()["decision"] == "ALLOW"

    anyio.run(send)


def test_openapi_documents_request_body_response_and_authentication() -> None:
    schema = create_app(Settings(environment="test")).openapi()
    operation = schema["paths"]["/v1/validate"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]
    assert "payload" in body["required"]
    assert "$ref" not in json.dumps(body)
    assert operation["security"] == [{"APIKeyHeader": []}]
    assert "ValidationResponse" in json.dumps(operation["responses"])
