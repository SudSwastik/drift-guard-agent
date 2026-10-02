"""Bounded, authenticated drift summaries and observation health counters."""

from datetime import datetime, timedelta
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Depends, Query, Request

from drift_guard.api.errors import ServiceError
from drift_guard.api.security import require_api_key
from drift_guard.contracts.models import Direction
from drift_guard.contracts.repository import ContractRepository, UnknownContractError
from drift_guard.drift.models import ReportScope, timestamp
from drift_guard.drift.report import build_report
from drift_guard.drift.service import ObservationService
from drift_guard.validation.engine import VALIDATOR_VERSION
from drift_guard.validation.policy import POLICY_VERSION

router = APIRouter(prefix="/v1", tags=["drift"], dependencies=[Depends(require_api_key)])


@router.get("/drift/{api}/{version}")
async def drift_summary(
    api: str,
    version: str,
    request: Request,
    operation: Annotated[str, Query(min_length=1, max_length=256)],
    direction: Direction,
    windowSeconds: Annotated[int, Query(ge=1, le=604800)] = 3600,
    start: datetime | None = None,
    end: datetime | None = None,
) -> dict[str, Any]:
    service: ObservationService = request.app.state.observation_service
    repository: ContractRepository = request.app.state.contract_repository
    settings = service.settings
    try:
        contract = repository.get(api, version, operation, direction)
    except UnknownContractError:
        raise ServiceError(404, "unknown_contract", "Unknown contract selection") from None
    now = service.clock()
    if (start is None) != (end is None):
        raise ServiceError(422, "invalid_window", "Provide both start and end, or neither.")
    window_end = end if end is not None else now
    window_start = start if start is not None else window_end - timedelta(seconds=windowSeconds)
    try:
        start_us, end_us = timestamp(window_start), timestamp(window_end)
    except ValueError:
        raise ServiceError(
            422, "invalid_window", "Window timestamps must include a timezone."
        ) from None
    if (
        start_us >= end_us
        or end_us > timestamp(now)
        or end_us - start_us > 604800 * 1_000_000
        or start_us < timestamp(now - timedelta(days=settings.observation_retention_days))
    ):
        raise ServiceError(
            422,
            "invalid_window",
            "Window must be within retention, at most 7 days, and end no later than now.",
        )
    scope = ReportScope(
        api=api,
        version=version,
        operation=operation,
        direction=direction,
        environment=settings.environment,
        contract_hash=contract.content_hash,
        policy_version=POLICY_VERSION,
        validator_version=VALIDATOR_VERSION,
        mode="enforce" if settings.blocking_enabled else "report_only",
        start=start_us,
        end=end_us,
    )
    try:
        with anyio.fail_after(settings.storage_timeout_seconds):
            aggregate = await service.repository.aggregate(scope)
    except Exception:
        raise ServiceError(
            503, "observation_storage_unavailable", "Drift observations are unavailable."
        ) from None
    report = build_report(
        scope,
        aggregate,
        settings.drift_min_samples,
        settings.drift_min_count,
        settings.drift_min_rate,
    )
    return {**report, "requestId": request.state.request_id}


@router.get("/observations/status")
async def observation_status(request: Request) -> dict[str, Any]:
    service: ObservationService = request.app.state.observation_service
    healthy = await service.health()
    return {
        "storageReady": healthy,
        "failureMode": service.settings.observation_failure_mode,
        "retentionDays": service.settings.observation_retention_days,
        "counters": dict(service.counters),
        "requestId": request.state.request_id,
    }
