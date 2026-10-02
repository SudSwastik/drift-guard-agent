"""Version 1 schema; metadata contains no request payloads or field paths."""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
)

SCHEMA_VERSION = 1
metadata = MetaData()
schema_version = Table(
    "guard_schema_version",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", Integer, nullable=False),
)
observations = Table(
    "guard_observations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("dedup_key", String(64), nullable=False, unique=True),
    Column("fingerprint", String(64), nullable=False),
    Column("occurred_at", BigInteger, nullable=False),
    Column("api", String(63), nullable=False),
    Column("version", String(64), nullable=False),
    Column("operation", String(256), nullable=False),
    Column("direction", String(8), nullable=False),
    Column("environment", String(16), nullable=False),
    Column("contract_hash", String(64), nullable=False),
    Column("policy_version", String(64), nullable=False),
    Column("validator_version", String(128), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("valid", Boolean, nullable=False),
    Column("decision", String(8), nullable=False),
    Column("severity", String(8), nullable=False),
)
codes = Table(
    "guard_observation_codes",
    metadata,
    Column(
        "observation_id",
        String(36),
        ForeignKey("guard_observations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("code", String(80), primary_key=True),
    Column("severity", String(8), nullable=False),
)
Index(
    "ix_guard_observation_scope",
    observations.c.api,
    observations.c.version,
    observations.c.operation,
    observations.c.direction,
    observations.c.environment,
    observations.c.contract_hash,
    observations.c.occurred_at,
)
Index("ix_guard_observation_retention", observations.c.occurred_at)
