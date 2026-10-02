"""Validate payloads with authentication, request limits, and killable CPU workers."""

import logging
from typing import Any

import anyio
from anyio import to_process
from fastapi import APIRouter, Depends, Request

from drift_guard.api.errors import ServiceError
from drift_guard.api.request_body import read_validation_request
from drift_guard.api.security import require_api_key
from drift_guard.config import Settings
from drift_guard.contracts.repository import ContractRepository, UnknownContractError
from drift_guard.validation.engine import validate_payload
from drift_guard.validation.models import ValidationRequest, ValidationResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["validation"], dependencies=[Depends(require_api_key)])

# Inline the optional context schema so Swagger has no dangling nested $defs references.
request_schema = ValidationRequest.model_json_schema()
context_schema = request_schema.pop("$defs")["ValidationContext"]
request_schema["properties"]["context"]["anyOf"][0] = context_schema


@router.post(
    "/validate",
    response_model=ValidationResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": request_schema}},
        }
    },
)
async def validate(request: Request) -> dict[str, Any]:
    settings: Settings = request.app.state.settings
    repository: ContractRepository = request.app.state.contract_repository
    slots: anyio.CapacityLimiter = request.app.state.validation_slots
    try:
        slots.acquire_nowait()
    except anyio.WouldBlock:
        raise ServiceError(429, "validation_busy", "Validation capacity is busy.") from None
    try:
        with anyio.fail_after(settings.validation_timeout_seconds):
            submitted = await read_validation_request(request, settings)
            try:
                contract = repository.get(
                    submitted.api, submitted.version, submitted.operation, submitted.direction
                )
            except UnknownContractError:
                raise ServiceError(404, "unknown_contract", "Unknown contract selection") from None
            result = await to_process.run_sync(
                validate_payload,
                contract,
                submitted.payload,
                settings.blocking_enabled,
                settings.max_findings,
                cancellable=True,
                limiter=request.app.state.validation_workers,
            )
    except TimeoutError:
        raise ServiceError(
            504, "validation_timeout", "Validation exceeded its time budget."
        ) from None
    except ServiceError:
        raise
    except Exception:
        logger.error("validation_unavailable", extra={"request_id": request.state.request_id})
        raise ServiceError(
            503, "validation_unavailable", "Validation is temporarily unavailable."
        ) from None
    finally:
        slots.release()
    logger.info(
        "payload_validated",
        extra={
            "request_id": request.state.request_id,
            "contract_id": result.contractId,
            "contract_hash": result.contractHash,
            "decision": result.decision,
            "validation_severity": result.severity,
            "finding_count": len(result.findings),
            "validator_version": result.validatorVersion,
            "policy_version": result.policyVersion,
        },
    )
    return {**result.model_dump(mode="json"), "requestId": request.state.request_id}
