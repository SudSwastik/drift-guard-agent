"""Explicit version selection and contract metadata."""

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from drift_guard.contracts.models import DIALECT, Direction
from drift_guard.contracts.repository import ContractRepository, UnknownContractError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/contracts", tags=["contracts"])


@router.get("/{api}/{version}")
def contract_metadata(
    api: str,
    version: str,
    request: Request,
    operation: Annotated[str, Query(min_length=1, max_length=256)],
    direction: Direction,
) -> JSONResponse:
    repository: ContractRepository = request.app.state.contract_repository
    try:
        contract = repository.get(api, version, operation, direction)
    except UnknownContractError:
        return JSONResponse(
            status_code=404,
            content={
                "error": {"code": "unknown_contract", "message": "Unknown contract selection"},
                "requestId": request.state.request_id,
            },
        )
    logger.info(
        "contract_selected",
        extra={
            "request_id": request.state.request_id,
            "contract_id": contract.contract_id,
            "contract_hash": contract.content_hash,
        },
    )
    return JSONResponse(
        content={
            "contractId": contract.contract_id,
            "api": contract.api,
            "version": contract.version,
            "operation": contract.operation,
            "direction": contract.direction,
            "contentHash": contract.content_hash,
            "schemaDialect": DIALECT,
            "requestId": request.state.request_id,
        }
    )
