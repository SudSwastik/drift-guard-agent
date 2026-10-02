"""Transactional async SQL repository, replaceable through a small protocol."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol

import anyio
from sqlalchemy import case, delete, event, func, insert, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from drift_guard.drift.models import (
    Aggregate,
    CodeCount,
    Observation,
    ObservationReceipt,
    ReportScope,
)
from drift_guard.storage.schema import SCHEMA_VERSION, codes, metadata, observations, schema_version


class IdempotencyConflict(ValueError):
    """A key already refers to a different request or set of validation rules."""


class SchemaVersionError(RuntimeError):
    """An unsupported schema must not be silently upgraded/downgraded."""


class ObservationRepository(Protocol):
    async def initialize(self) -> None: ...
    async def record(self, observation: Observation) -> ObservationReceipt: ...
    async def aggregate(self, scope: ReportScope) -> Aggregate: ...
    async def purge(self, before: int, batch_size: int = 1000) -> int: ...
    async def health(self) -> bool: ...
    async def close(self) -> None: ...


class SQLObservationRepository:
    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        self.sqlite = url.drivername == "sqlite+aiosqlite"
        if url.drivername not in ("sqlite+aiosqlite", "postgresql+asyncpg"):
            raise ValueError("Only asynchronous SQLite/PostgreSQL URLs are supported")
        options: dict[str, Any] = {"echo": False, "hide_parameters": True}
        if self.sqlite:
            if url.database and url.database != ":memory:":
                Path(url.database).parent.mkdir(parents=True, exist_ok=True)
                options.update(pool_size=1, max_overflow=0, pool_timeout=1)
            options["connect_args"] = {"timeout": 0.5}
        else:
            options.update(pool_size=2, max_overflow=0, pool_timeout=1, pool_pre_ping=True)
            options["connect_args"] = {
                "timeout": 2,
                "command_timeout": 2,
                "server_settings": {"statement_timeout": "1500", "lock_timeout": "500"},
            }
        self.engine = create_async_engine(url, **options)
        self._initialize_lock = anyio.Lock()
        self._sqlite_lock = anyio.Lock()
        self._initialized = False
        if self.sqlite:

            @event.listens_for(self.engine.sync_engine, "connect")
            def sqlite_pragmas(connection: Any, record: Any) -> None:
                cursor = connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute("PRAGMA busy_timeout=500")
                cursor.close()

    @asynccontextmanager
    async def _guard(self) -> AsyncIterator[None]:
        if self.sqlite:
            async with self._sqlite_lock:
                yield
        else:
            yield

    async def initialize(self) -> None:
        async with self._initialize_lock:
            if self._initialized:
                return
            async with self.engine.begin() as connection:
                if not self.sqlite:
                    await connection.exec_driver_sql("SELECT pg_advisory_xact_lock(874018941)")
                await connection.run_sync(lambda sync: schema_version.create(sync, checkfirst=True))
                current = await connection.scalar(
                    select(schema_version.c.version).where(schema_version.c.id == 1)
                )
                if current is not None and current != SCHEMA_VERSION:
                    raise SchemaVersionError("Unsupported observation database schema version")
                await connection.run_sync(metadata.create_all)
                factory = sqlite_insert if self.sqlite else pg_insert
                await connection.execute(
                    factory(schema_version)
                    .values(id=1, version=SCHEMA_VERSION)
                    .on_conflict_do_nothing(index_elements=["id"])
                )
            self._initialized = True

    async def record(self, observation: Observation) -> ObservationReceipt:
        await self.initialize()
        values = asdict(observation)
        values.pop("codes")
        factory = sqlite_insert if self.sqlite else pg_insert
        async with self._guard(), self.engine.begin() as connection:
            statement = (
                factory(observations)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["dedup_key"])
                .returning(observations.c.id)
            )
            created = await connection.scalar(statement)
            if created is None:
                existing = (
                    await connection.execute(
                        select(
                            observations.c.id,
                            observations.c.fingerprint,
                        ).where(observations.c.dedup_key == observation.dedup_key)
                    )
                ).one()
                if existing.fingerprint != observation.fingerprint:
                    raise IdempotencyConflict(
                        "Idempotency key already refers to a different request"
                    )
                return ObservationReceipt(status="duplicate", id=existing.id)
            if observation.codes:
                await connection.execute(
                    insert(codes),
                    [
                        {"observation_id": observation.id, "code": code, "severity": severity}
                        for code, severity in observation.codes
                    ],
                )
            return ObservationReceipt(status="stored", id=observation.id)

    async def aggregate(self, scope: ReportScope) -> Aggregate:
        await self.initialize()
        conditions = [
            observations.c.occurred_at >= scope.start,
            observations.c.occurred_at < scope.end,
        ]
        for field, value in asdict(scope).items():
            if field not in ("start", "end"):
                conditions.append(observations.c[field] == value)
        base = (
            select(observations.c.id, observations.c.occurred_at, observations.c.valid)
            .where(*conditions)
            .cte("selected_events")
        )
        totals = (
            select(
                func.count().label("samples"),
                func.coalesce(func.sum(case((base.c.valid.is_(False), 1), else_=0)), 0).label(
                    "invalid"
                ),
                func.min(base.c.occurred_at).label("first_seen"),
                func.max(base.c.occurred_at).label("last_seen"),
            )
            .select_from(base)
            .cte("totals")
        )
        grouped = (
            select(
                codes.c.code,
                func.count().label("count"),
                func.min(base.c.occurred_at).label("first_seen"),
                func.max(base.c.occurred_at).label("last_seen"),
            )
            .select_from(base.join(codes, base.c.id == codes.c.observation_id))
            .group_by(codes.c.code)
            .cte("code_counts")
        )
        statement = (
            select(
                totals,
                grouped.c.code,
                grouped.c.count.label("code_count"),
                grouped.c.first_seen.label("code_first"),
                grouped.c.last_seen.label("code_last"),
            )
            .select_from(totals.outerjoin(grouped, true()))
            .order_by(grouped.c.code)
        )
        # One statement gives totals and finding counts from the same database snapshot.
        async with self._guard(), self.engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        first = rows[0]
        return Aggregate(
            samples=first["samples"],
            invalid=first["invalid"],
            first_seen=first["first_seen"],
            last_seen=first["last_seen"],
            codes=tuple(
                CodeCount(row["code"], row["code_count"], row["code_first"], row["code_last"])
                for row in rows
                if row["code"] is not None
            ),
        )

    async def purge(self, before: int, batch_size: int = 1000) -> int:
        if not 1 <= batch_size <= 1000:
            raise ValueError("Retention batches must contain 1 to 1000 observations")
        await self.initialize()
        oldest = (
            select(observations.c.id)
            .where(observations.c.occurred_at < before)
            .order_by(observations.c.occurred_at)
            .limit(batch_size)
        )
        async with self._guard(), self.engine.begin() as connection:
            result = await connection.execute(
                delete(observations)
                .where(observations.c.id.in_(oldest))
                .returning(observations.c.id)
            )
            return len(result.all())

    async def health(self) -> bool:
        await self.initialize()
        async with self._guard(), self.engine.connect() as connection:
            return (
                await connection.scalar(
                    select(schema_version.c.version).where(schema_version.c.id == 1)
                )
                == SCHEMA_VERSION
            )

    async def close(self) -> None:
        await self.engine.dispose()
        self._initialized = False
