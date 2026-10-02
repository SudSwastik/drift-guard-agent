"""Contract access interface and eager file-backed implementation."""

import logging
from pathlib import Path
from typing import Protocol

from drift_guard.contracts.loader import ContractLoadError, load_contract
from drift_guard.contracts.models import Contract, Direction

logger = logging.getLogger(__name__)
BUNDLED_CONTRACTS = Path(__file__).parent / "fixtures"


class UnknownContractError(LookupError):
    """No contract matches all explicitly selected identity fields."""


class ContractRepository(Protocol):
    def get(self, api: str, version: str, operation: str, direction: Direction) -> Contract:
        """Resolve an exact contract identity without fallback."""
        ...


class FileContractRepository:
    """Snapshot trusted deployment files; reloads require a new application instance."""

    def __init__(self, directory: Path = BUNDLED_CONTRACTS) -> None:
        self._contracts: dict[tuple[str, str, str, Direction], Contract] = {}
        paths = sorted(directory.glob("*.json"))
        if not paths:
            raise ContractLoadError("Configured contract directory has no JSON contracts")
        for path in paths:
            contract = load_contract(path)
            key = (contract.api, contract.version, contract.operation, contract.direction)
            if key in self._contracts:
                raise ContractLoadError(f"Duplicate contract identity: {contract.contract_id}")
            self._contracts[key] = contract
        for contract in self._contracts.values():
            logger.info(
                "contract_loaded",
                extra={"contract_id": contract.contract_id, "contract_hash": contract.content_hash},
            )

    def get(self, api: str, version: str, operation: str, direction: Direction) -> Contract:
        try:
            return self._contracts[(api, version, operation, direction)]
        except KeyError:
            raise UnknownContractError("Unknown contract selection") from None
