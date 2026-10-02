"""End-to-end persistence, idempotency, privacy, outage policies, and recovery."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from drift_guard.api.application import create_app
from drift_guard.config import Settings
from drift_guard.drift.models import Aggregate, Observation, ObservationReceipt, ReportScope
from drift_guard.storage.repository import SQLObservationRepository
from drift_guard.storage.schema import codes, observations
from tests.client import managed_client
from tests.test_validation import CASES
from tests.test_validation_api import envelope

QUERY = "/v1/drift/payments/v1?operation=POST%20%2Fpayments&direction=request"


class FaultRepository(SQLObservationRepository):
    offline = False

    async def initialize(self) -> None:
        if self.offline:
            raise RuntimeError("private-database-detail")
        await super().initialize()

    async def health(self) -> bool:
        return False if self.offline else await super().health()

    async def record(self, observation: Observation) -> ObservationReceipt:
        if self.offline:
            raise RuntimeError("private-database-detail")
        return await super().record(observation)

    async def aggregate(self, scope: ReportScope) -> Aggregate:
        if self.offline:
            raise RuntimeError("private-database-detail")
        return await super().aggregate(scope)


def test_repeat_event_is_one_sample_and_changed_request_conflicts() -> None:
    app = create_app(Settings(environment="test"))

    async def scenario() -> None:
        async with managed_client(app) as client:
            headers = {"Idempotency-Key": "repeat-event"}
            body = envelope(CASES[1]["payload"])
            first = await client.post("/v1/validate", json=body, headers=headers)
            assert first.status_code == 200
            assert first.json()["observation"]["status"] == "stored"
            for _ in range(5):
                duplicate = await client.post("/v1/validate", json=body, headers=headers)
                assert duplicate.json()["observation"] == {
                    "status": "duplicate",
                    "id": first.json()["observation"]["id"],
                }
            conflicting = await client.post(
                "/v1/validate", json=envelope(CASES[0]["payload"]), headers=headers
            )
            assert conflicting.status_code == 409
            report = (await client.get(QUERY)).json()
            assert report["sampleCount"] == 1 and report["invalidCount"] == 1
            assert report["status"] == "insufficient_data" and not report["driftDetected"]
            status = (await client.get("/v1/observations/status")).json()
            assert status["counters"]["stored"] == 1 and status["counters"]["duplicate"] == 5

    anyio.run(scenario)


def test_trend_thresholds_and_metadata_are_exact_with_fixed_clock() -> None:
    app = create_app(Settings(environment="test"))
    now = [datetime(2026, 1, 1, 12, tzinfo=UTC)]
    app.state.observation_service.clock = lambda: now[0]

    async def scenario() -> None:
        async with managed_client(app) as client:
            for index in range(10):
                payload = CASES[1 if index < 3 else 0]["payload"]
                response = await client.post(
                    "/v1/validate",
                    json=envelope(payload),
                    headers={"Idempotency-Key": f"event-{index}"},
                )
                assert response.json()["observation"]["status"] == "stored"
                now[0] += timedelta(seconds=1)
            response = await client.get(QUERY)
            report = response.json()
            assert report["sampleCount"] == 10 and report["invalidCount"] == 3
            assert report["status"] == "threshold_exceeded" and report["driftDetected"]
            assert report["firstSeen"] == "2026-01-01T12:00:00+00:00"
            assert report["lastSeen"] == "2026-01-01T12:00:09+00:00"
            assert report["requestId"] == response.headers["x-request-id"]
            assert report["environment"] == "test"
            assert all(item["count"] == 3 and item["rate"] == 0.3 for item in report["findings"])
            assert (await client.get(QUERY)).json()["findings"] == report["findings"]
            # Half-open window includes t=0, excludes t=2.
            bounded = await client.get(
                QUERY,
                params={
                    "operation": "POST /payments",
                    "direction": "request",
                    "start": "2026-01-01T12:00:00Z",
                    "end": "2026-01-01T12:00:02Z",
                },
            )
            assert bounded.json()["sampleCount"] == 2

    anyio.run(scenario)


def test_stored_rows_omit_payload_values_paths_headers_and_context() -> None:
    app = create_app(Settings(environment="test"))

    async def scenario() -> None:
        async with managed_client(app) as client:
            payload = {**CASES[0]["payload"], "private-field-name": "private-field-value"}
            response = await client.post(
                "/v1/validate",
                json=envelope(
                    payload,
                    context={"clientId": "private-client-id", "environment": "production"},
                ),
                headers={
                    "Idempotency-Key": "private-header-key",
                    "X-Request-ID": "private-request-id",
                },
            )
            assert response.status_code == 200
            repository = app.state.observation_service.repository
            async with repository.engine.connect() as connection:
                rows = (await connection.execute(select(observations))).mappings().all()
                code_rows = (await connection.execute(select(codes))).mappings().all()
            persisted = json.dumps([dict(row) for row in [*rows, *code_rows]])
            for private in (
                "private-field-name",
                "private-field-value",
                "private-client-id",
                "private-header-key",
                "private-request-id",
                "C123",
            ):
                assert private not in persisted
            assert (
                rows[0]["environment"] == "test"
            )  # A caller cannot relabel the service environment.
            assert len(rows[0]["fingerprint"]) == len(rows[0]["dedup_key"]) == 64

    anyio.run(scenario)


@pytest.mark.parametrize("required", [False, True])
def test_storage_outage_response_logs_counters_and_recovery(
    required: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    repository = FaultRepository("sqlite+aiosqlite:///:memory:")
    app = create_app(
        Settings(
            environment="test",
            observation_failure_mode="required" if required else "best_effort",
        ),
        observation_repository=repository,
    )

    async def scenario() -> None:
        async with managed_client(app) as client:
            repository.offline = True
            data = envelope(CASES[0]["payload"])
            response = await client.post(
                "/v1/validate", json=data, headers={"Idempotency-Key": "recovery"}
            )
            assert response.status_code == (503 if required else 200)
            if not required:
                assert response.json()["decision"] == "ALLOW"
                assert response.json()["observation"]["status"] == "unavailable"
            assert (await client.get(QUERY)).status_code == 503
            assert (await client.get("/health/ready")).status_code == (503 if required else 200)
            status = (await client.get("/v1/observations/status")).json()
            assert not status["storageReady"] and status["counters"]["writeFailures"] == 1
            repository.offline = False
            recovered = await client.post(
                "/v1/validate", json=data, headers={"Idempotency-Key": "recovery"}
            )
            assert (
                recovered.status_code == 200
                and recovered.json()["observation"]["status"] == "stored"
            )
            assert (await client.get(QUERY)).json()["sampleCount"] == 1

    anyio.run(scenario)
    logs = capsys.readouterr().err
    assert '"observation_write_failed"' in logs
    assert "private-database-detail" not in logs


def test_required_storage_outage_fails_startup() -> None:
    repository = FaultRepository("sqlite+aiosqlite:///:memory:")
    repository.offline = True
    app = create_app(
        Settings(environment="test", observation_failure_mode="required"),
        observation_repository=repository,
    )

    async def scenario() -> None:
        with pytest.raises(RuntimeError, match="Required observation storage is unavailable"):
            async with managed_client(app):
                pass

    anyio.run(scenario)


def test_restart_preserves_idempotency_and_local_fingerprint_key(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url=SecretStr(f"sqlite+aiosqlite:///{tmp_path / 'persistent.sqlite'}"),
    )

    async def scenario() -> None:
        first = create_app(settings)
        async with managed_client(first) as client:
            response = await client.post(
                "/v1/validate",
                json=envelope(CASES[0]["payload"]),
                headers={"Idempotency-Key": "across-restart"},
            )
            receipt = response.json()["observation"]
        second = create_app(settings)
        async with managed_client(second) as client:
            response = await client.post(
                "/v1/validate",
                json=envelope(CASES[0]["payload"]),
                headers={"Idempotency-Key": "across-restart"},
            )
            assert response.json()["observation"] == {"status": "duplicate", "id": receipt["id"]}
            assert (await client.get(QUERY)).json()["sampleCount"] == 1
        assert len((tmp_path / "observation-hmac.key").read_bytes()) == 32
        assert (tmp_path / "observation-hmac.key").stat().st_mode & 0o777 == 0o600

    anyio.run(scenario)


@pytest.mark.parametrize(
    "extra",
    [
        {"start": "2026-01-01T00:00:00Z"},
        {"start": "2026-01-01T00:00:00", "end": "2026-01-01T01:00:00"},
        {"start": "2099-01-01T00:00:00Z", "end": "2099-01-01T01:00:00Z"},
        {"windowSeconds": "604801"},
    ],
)
def test_report_windows_are_bounded(extra: dict[str, str]) -> None:
    app = create_app(Settings(environment="test"))

    async def scenario() -> None:
        async with managed_client(app) as client:
            response = await client.get(
                "/v1/drift/payments/v1",
                params={"operation": "POST /payments", "direction": "request", **extra},
            )
            assert response.status_code == 422

    anyio.run(scenario)


def test_drift_and_storage_status_require_configured_authentication() -> None:
    app = create_app(
        Settings(environment="test", api_key=SecretStr("test-key-with-at-least-32-characters"))
    )

    async def scenario() -> None:
        async with managed_client(app) as client:
            assert (await client.get(QUERY)).status_code == 401
            assert (await client.get("/v1/observations/status")).status_code == 401

    anyio.run(scenario)
