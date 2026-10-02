"""Same repository contract against file SQLite and an optional isolated PostgreSQL server."""

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import anyio
import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from drift_guard.drift.models import Observation, ReportScope
from drift_guard.storage.repository import (
    IdempotencyConflict,
    SchemaVersionError,
    SQLObservationRepository,
)
from drift_guard.storage.schema import codes, observations, schema_version


@pytest.fixture(params=["sqlite", "postgresql"])
def database_url(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    if request.param == "sqlite":
        yield f"sqlite+aiosqlite:///{tmp_path / 'observations.sqlite'}"
        return
    configured = os.environ.get("DRIFT_GUARD_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set DRIFT_GUARD_TEST_POSTGRES_URL for isolated PostgreSQL integration checks")
    admin_url = make_url(configured)
    database = "guard_test_" + uuid4().hex

    async def create_or_drop(create: bool) -> None:
        engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
        try:
            async with engine.connect() as connection:
                # The identifier consists only of a fixed prefix and generated hex digits.
                action = "CREATE DATABASE" if create else "DROP DATABASE"
                await connection.exec_driver_sql(f'{action} "{database}"')
        finally:
            await engine.dispose()

    anyio.run(create_or_drop, True)
    try:
        yield admin_url.set(database=database).render_as_string(hide_password=False)
    finally:
        anyio.run(create_or_drop, False)


def observation(**changes: Any) -> Observation:
    event = Observation(
        id=str(uuid4()),
        dedup_key=uuid4().hex,
        fingerprint="a" * 64,
        occurred_at=100,
        api="payments",
        version="v1",
        operation="POST /payments",
        direction="request",
        environment="test",
        contract_hash="b" * 64,
        policy_version="policy-v1",
        validator_version="validator-v1",
        mode="report_only",
        valid=False,
        decision="WARN",
        severity="HIGH",
        codes=(("invalid_type", "HIGH"),),
    )
    return replace(event, **changes)


def scope(**changes: Any) -> ReportScope:
    return replace(
        ReportScope(
            api="payments",
            version="v1",
            operation="POST /payments",
            direction="request",
            environment="test",
            contract_hash="b" * 64,
            policy_version="policy-v1",
            validator_version="validator-v1",
            mode="report_only",
            start=0,
            end=1000,
        ),
        **changes,
    )


def test_record_duplicate_and_conflict(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        try:
            event = observation()
            first = await repository.record(event)
            duplicate = await repository.record(replace(event, id=str(uuid4())))
            assert first.status == "stored" and duplicate.status == "duplicate"
            assert first.id == duplicate.id
            with pytest.raises(IdempotencyConflict):
                await repository.record(replace(event, fingerprint="c" * 64))
            result = await repository.aggregate(scope())
            assert result.samples == result.invalid == result.codes[0].count == 1
        finally:
            await repository.close()

    anyio.run(scenario)


def test_concurrent_retries_count_once(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        event = observation()
        receipts = []

        async def record() -> None:
            receipts.append(await repository.record(replace(event, id=str(uuid4()))))

        try:
            await repository.initialize()
            async with anyio.create_task_group() as group:
                for _ in range(10):
                    group.start_soon(record)
            assert sum(receipt.status == "stored" for receipt in receipts) == 1
            assert (await repository.aggregate(scope())).samples == 1
        finally:
            await repository.close()

    anyio.run(scenario)


def test_record_failure_rolls_back_observation_and_codes(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        try:
            duplicate_codes = (("invalid_type", "HIGH"), ("invalid_type", "HIGH"))
            with pytest.raises(IntegrityError):
                await repository.record(observation(codes=duplicate_codes))
            assert (await repository.aggregate(scope())).samples == 0
            async with repository.engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(codes)) == 0
        finally:
            await repository.close()

    anyio.run(scenario)


def test_window_boundaries_and_scope_isolation(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        try:
            for event in (
                observation(occurred_at=99),
                observation(occurred_at=100),
                observation(occurred_at=199),
                observation(occurred_at=200),
                observation(environment="production"),
                observation(contract_hash="x" * 64),
                observation(policy_version="another-policy"),
                observation(validator_version="another-validator"),
                observation(mode="enforce"),
                observation(
                    occurred_at=150, valid=True, codes=(), decision="ALLOW", severity="NONE"
                ),
            ):
                await repository.record(event)
            result = await repository.aggregate(scope(start=100, end=200))
            assert result.samples == 3 and result.invalid == 2
            assert result.first_seen == 100 and result.last_seen == 199
            assert result.codes[0].count == 2
            assert result.codes[0].first_seen == 100 and result.codes[0].last_seen == 199
        finally:
            await repository.close()

    anyio.run(scenario)


def test_retention_is_bounded_and_cascades_code_deletion(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        try:
            for at in (98, 99, 100):
                await repository.record(observation(occurred_at=at))
            assert await repository.purge(100, batch_size=1) == 1
            assert await repository.purge(100) == 1
            assert await repository.purge(100) == 0
            assert (await repository.aggregate(scope())).samples == 1
            async with repository.engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(codes)) == 1
            with pytest.raises(ValueError):
                await repository.purge(100, batch_size=1001)
        finally:
            await repository.close()

    anyio.run(scenario)


def test_restart_preserves_events_and_migration_is_repeatable(database_url: str) -> None:
    async def scenario() -> None:
        first = SQLObservationRepository(database_url)
        await first.initialize()
        await first.record(observation())
        await first.close()
        second = SQLObservationRepository(database_url)
        try:
            await second.initialize()
            await second.initialize()
            assert await second.health()
            assert (await second.aggregate(scope())).samples == 1
            async with second.engine.connect() as connection:
                assert await connection.scalar(select(schema_version.c.version)) == 1
        finally:
            await second.close()

    anyio.run(scenario)


def test_unknown_schema_version_is_rejected(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        await repository.initialize()
        async with repository.engine.begin() as connection:
            await connection.execute(update(schema_version).values(version=999))
        await repository.close()
        try:
            with pytest.raises(SchemaVersionError):
                await repository.initialize()
        finally:
            await repository.close()

    anyio.run(scenario)


def test_empty_aggregate_is_not_a_missing_dependency(database_url: str) -> None:
    async def scenario() -> None:
        repository = SQLObservationRepository(database_url)
        try:
            assert await repository.health()
            result = await repository.aggregate(scope())
            assert result.samples == result.invalid == 0
            assert result.codes == () and result.first_seen is None and result.last_seen is None
            async with repository.engine.begin() as connection:
                await connection.execute(delete(observations))
        finally:
            await repository.close()

    anyio.run(scenario)
