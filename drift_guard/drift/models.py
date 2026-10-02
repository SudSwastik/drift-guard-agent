"""Privacy-safe observation and exact aggregation scope."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp(value: datetime) -> int:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return int(value.timestamp() * 1_000_000)


def iso_timestamp(value: int | None) -> str | None:
    return datetime.fromtimestamp(value / 1_000_000, UTC).isoformat() if value is not None else None


@dataclass(frozen=True)
class Observation:
    id: str
    dedup_key: str
    fingerprint: str
    occurred_at: int
    api: str
    version: str
    operation: str
    direction: str
    environment: str
    contract_hash: str
    policy_version: str
    validator_version: str
    mode: str
    valid: bool
    decision: str
    severity: str
    codes: tuple[tuple[str, str], ...]


class ObservationReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["stored", "duplicate", "unavailable"]
    id: str | None = None


@dataclass(frozen=True)
class ReportScope:
    api: str
    version: str
    operation: str
    direction: str
    environment: str
    contract_hash: str
    policy_version: str
    validator_version: str
    mode: str
    start: int
    end: int


@dataclass(frozen=True)
class CodeCount:
    code: str
    count: int
    first_seen: int
    last_seen: int


@dataclass(frozen=True)
class Aggregate:
    samples: int
    invalid: int
    first_seen: int | None
    last_seen: int | None
    codes: tuple[CodeCount, ...]
