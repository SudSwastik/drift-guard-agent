"""Persistence budgets, redaction, idempotency, and visible outage behavior."""

import hashlib
import hmac
import json
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from uuid import uuid4

import anyio

from drift_guard.config import Settings
from drift_guard.drift.models import Observation, ObservationReceipt, timestamp, utc_now
from drift_guard.storage.repository import (
    IdempotencyConflict,
    ObservationRepository,
    SchemaVersionError,
)
from drift_guard.validation.models import ValidationRequest, ValidationResult

logger = logging.getLogger(__name__)


class PersistenceUnavailable(RuntimeError):
    """Required observation storage failed; callers must retry with the same key."""


class ObservationService:
    def __init__(
        self,
        repository: ObservationRepository,
        settings: Settings,
        hash_key: bytes,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.settings = settings
        self._hash_key = hash_key
        self.clock = clock
        self.ready = False
        self.counters = {
            "stored": 0,
            "duplicate": 0,
            "writeFailures": 0,
            "retentionDeleted": 0,
            "retentionFailures": 0,
        }

    def _digest(self, text: str) -> str:
        return hmac.new(self._hash_key, text.encode("utf-8"), hashlib.sha256).hexdigest()

    async def start(self) -> None:
        try:
            with anyio.fail_after(self.settings.storage_timeout_seconds):
                await self.repository.initialize()
            self.ready = True
        except SchemaVersionError:
            raise RuntimeError("Unsupported observation database schema version") from None
        except Exception as error:
            self.ready = False
            logger.error(
                "observation_storage_start_failed",
                extra={"storage_error_type": type(error).__name__},
            )
            if self.settings.observation_failure_mode == "required":
                raise RuntimeError("Required observation storage is unavailable") from None
        if self.ready:
            await self.prune()

    async def record(
        self,
        submitted: ValidationRequest,
        result: ValidationResult,
        idempotency_key: str | None,
        request_id: str,
    ) -> ObservationReceipt:
        # Scope keys to the selected contract/environment; never persist caller header text.
        scope = f"{self.settings.environment}:{result.contractId}"
        key = idempotency_key or str(uuid4())
        canonical = json.dumps(
            {
                "request": submitted.model_dump(),
                "contractHash": result.contractHash,
                "policyVersion": result.policyVersion,
                "validatorVersion": result.validatorVersion,
                "mode": result.mode,
                "result": result.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        observation = Observation(
            id=str(uuid4()),
            dedup_key=self._digest(f"key:{scope}:{key}"),
            fingerprint=self._digest(f"request:{scope}:{canonical}"),
            occurred_at=timestamp(self.clock()),
            api=submitted.api,
            version=submitted.version,
            operation=submitted.operation,
            direction=submitted.direction,
            environment=self.settings.environment,
            contract_hash=result.contractHash,
            policy_version=result.policyVersion,
            validator_version=result.validatorVersion,
            mode=result.mode,
            valid=result.valid,
            decision=result.decision,
            severity=result.severity,
            # Paths can themselves be sensitive. Keep only one occurrence of each code per event.
            codes=tuple(sorted({(finding.code, finding.severity) for finding in result.findings})),
        )
        try:
            with anyio.fail_after(self.settings.storage_timeout_seconds):
                receipt = await self.repository.record(observation)
            self.ready = True
            self.counters[receipt.status] += 1
        except IdempotencyConflict:
            raise
        except Exception:
            self.ready = False
            self.counters["writeFailures"] += 1
            logger.error("observation_write_failed", extra={"request_id": request_id})
            if self.settings.observation_failure_mode == "required":
                raise PersistenceUnavailable(
                    "Required observation storage is unavailable"
                ) from None
            return ObservationReceipt(status="unavailable")
        logger.info(
            "observation_recorded",
            extra={
                "request_id": request_id,
                "observation_id": receipt.id,
                "observation_status": receipt.status,
            },
        )
        return receipt

    async def health(self) -> bool:
        try:
            with anyio.fail_after(self.settings.storage_timeout_seconds):
                self.ready = await self.repository.health()
        except Exception:
            self.ready = False
        return self.ready

    async def prune(self) -> int:
        cutoff = timestamp(self.clock() - timedelta(days=self.settings.observation_retention_days))
        try:
            with anyio.fail_after(self.settings.storage_timeout_seconds):
                count = await self.repository.purge(cutoff)
            self.counters["retentionDeleted"] += count
            return count
        except Exception:
            self.counters["retentionFailures"] += 1
            logger.error("observation_retention_failed")
            return 0

    async def retention_loop(self) -> None:
        while True:
            await anyio.sleep(self.settings.retention_interval_seconds)
            await self.prune()
