"""Standard JSON Schema validation mapped to deterministic finding codes."""

import re
from collections.abc import Iterator, Mapping, Sequence
from importlib.metadata import version
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError

from drift_guard.contracts.models import Contract
from drift_guard.validation.models import Finding, Severity, ValidationResult
from drift_guard.validation.policy import POLICY_VERSION, decide

VALIDATOR_VERSION = f"jsonschema-{version('jsonschema')}:mapping-v1"
# Every message is fixed text: validator error messages can contain payload values.
RULES: dict[str, tuple[str, Severity, str]] = {
    "required": ("missing_required_field", "HIGH", "Required field is missing."),
    "additionalProperties": ("unexpected_field", "LOW", "Field is not allowed by the contract."),
    "type": ("invalid_type", "HIGH", "Value has an incorrect JSON type."),
    "enum": ("invalid_enum", "MEDIUM", "Value is not one of the allowed options."),
    "const": ("invalid_constant", "MEDIUM", "Value does not match the required constant."),
    "minimum": ("below_minimum", "MEDIUM", "Number is below the minimum."),
    "maximum": ("above_maximum", "MEDIUM", "Number is above the maximum."),
    "exclusiveMinimum": ("below_exclusive_minimum", "MEDIUM", "Number must exceed the minimum."),
    "exclusiveMaximum": ("above_exclusive_maximum", "MEDIUM", "Number must be below the maximum."),
    "multipleOf": ("invalid_multiple", "MEDIUM", "Number is not an allowed multiple."),
    "format": ("invalid_format", "MEDIUM", "Value does not match the required format."),
    "pattern": ("invalid_pattern", "MEDIUM", "String does not match the required pattern."),
    "minLength": ("string_too_short", "MEDIUM", "String is shorter than allowed."),
    "maxLength": ("string_too_long", "MEDIUM", "String is longer than allowed."),
    "minItems": ("too_few_items", "MEDIUM", "Array contains too few items."),
    "maxItems": ("too_many_items", "MEDIUM", "Array contains too many items."),
    "uniqueItems": ("duplicate_items", "MEDIUM", "Array items must be unique."),
}


def _pointer(parts: Sequence[str | int]) -> str:
    """RFC 6901 JSON Pointer; the empty string identifies the payload root."""
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _findings(error: ValidationError) -> Iterator[Finding]:
    code, severity, message = RULES.get(
        str(error.validator), ("schema_violation", "HIGH", "Payload violates the contract.")
    )
    path = list(error.absolute_path)
    if (
        error.validator == "required"
        and isinstance(error.instance, dict)
        and isinstance(error.validator_value, list)
    ):
        for field in sorted(set(error.validator_value) - error.instance.keys()):
            yield Finding(
                code=code, path=_pointer([*path, field]), severity=severity, message=message
            )
    elif (
        error.validator == "additionalProperties"
        and error.validator_value is False
        and isinstance(error.instance, dict)
        and isinstance(error.schema, Mapping)
    ):
        properties = error.schema.get("properties", {})
        patterns = error.schema.get("patternProperties", {})
        for field in sorted(error.instance):
            if field not in properties and not any(
                re.search(pattern, field) for pattern in patterns
            ):
                yield Finding(
                    code=code, path=_pointer([*path, field]), severity=severity, message=message
                )
    else:
        yield Finding(code=code, path=_pointer(path), severity=severity, message=message)


def validate_payload(
    contract: Contract, payload: Any, blocking_enabled: bool, max_findings: int
) -> ValidationResult:
    """Importable worker function; preserves JSON types and never returns payload values."""
    validator = Draft202012Validator(contract.schema, format_checker=FormatChecker())
    found: dict[tuple[str, str], Finding] = {}
    truncated = False
    for error in validator.iter_errors(payload):
        for finding in _findings(error):
            key = (finding.path, finding.code)
            if key in found:
                continue
            if len(found) >= max_findings:
                truncated = True
                break
            found[key] = finding
        if truncated:
            break
    findings = sorted(found.values(), key=lambda finding: (finding.path, finding.code))
    if truncated:
        # Reserve one output slot for a conservative limit finding; never claim ALLOW.
        findings = findings[: max_findings - 1]
        findings.append(
            Finding(
                code="finding_limit_exceeded",
                path="",
                severity="HIGH",
                message="More violations exist than the configured finding limit.",
            )
        )
    decision, severity = decide(findings, blocking_enabled)
    return ValidationResult(
        valid=not findings,
        decision=decision,
        severity=severity,
        mode="enforce" if blocking_enabled else "report_only",
        contractId=contract.contract_id,
        contractHash=contract.content_hash,
        validatorVersion=VALIDATOR_VERSION,
        policyVersion=POLICY_VERSION,
        findings=tuple(findings),
        findingsTruncated=truncated,
    )
