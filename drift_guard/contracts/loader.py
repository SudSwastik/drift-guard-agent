"""Validate bundled/configured documents before accepting traffic."""

import hashlib
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from pydantic import ValidationError

from drift_guard.contracts.models import DIALECT, Contract, ContractDocument


class ContractLoadError(ValueError):
    """A configured contract is unusable; startup must fail."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractLoadError("Duplicate JSON keys are not allowed")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContractLoadError("Non-finite JSON numbers are not allowed")


def _check_references(value: Any) -> None:
    # Feature 2 supports self-contained schemas only. Never fetch network resources.
    if isinstance(value, dict):
        if isinstance(value.get("$ref"), str) or isinstance(value.get("$dynamicRef"), str):
            raise ContractLoadError("Schema references are not supported; use an inline schema")
        if isinstance(value.get("format"), str) and value["format"] not in FormatChecker.checkers:
            raise ContractLoadError("Schema uses an unsupported format")
        for child in value.values():
            _check_references(child)
    elif isinstance(value, list):
        for child in value:
            _check_references(child)


def load_contract(path: Path) -> Contract:
    """Hash normalized envelope content; formatting/key order do not affect identity."""
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        document = ContractDocument.model_validate(raw)
        if document.schema_document.get("$schema") != DIALECT:
            raise ContractLoadError("Contract must declare JSON Schema draft 2020-12")
        _check_references(document.schema_document)
        Draft202012Validator.check_schema(document.schema_document)
        canonical = json.dumps(
            document.model_dump(by_alias=True),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (OSError, ValueError, SchemaError, ValidationError):
        # Do not include submitted document contents in startup logs/errors.
        raise ContractLoadError(
            f"Cannot load contract {path.name}: invalid document or schema"
        ) from None
    return Contract(
        api=document.api,
        version=document.version,
        operation=document.operation,
        direction=document.direction,
        content_hash=hashlib.sha256(canonical).hexdigest(),
        canonical_document=canonical,
    )
