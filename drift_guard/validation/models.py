"""Typed validation inputs and stable, privacy-safe outputs."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from drift_guard.contracts.models import Direction
from drift_guard.drift.models import ObservationReceipt

Severity = Literal["NONE", "LOW", "MEDIUM", "HIGH"]
Decision = Literal["ALLOW", "WARN", "BLOCK"]


class ValidationContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    environment: Literal["local", "test", "staging", "production"] | None = None
    clientId: str | None = Field(default=None, min_length=1, max_length=128)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    api: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")
    version: str = Field(pattern=r"^v[1-9][0-9]*$")
    operation: str = Field(
        pattern=r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /\S*$", max_length=256
    )
    direction: Direction
    payload: Any = Field(..., description="The original JSON payload; no type coercion is applied.")
    context: ValidationContext | None = None


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    path: str
    severity: Severity
    message: str


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    decision: Decision
    severity: Severity
    mode: Literal["report_only", "enforce"]
    contractId: str
    contractHash: str
    validatorVersion: str
    policyVersion: str
    findings: tuple[Finding, ...]
    findingsTruncated: bool


class ValidationResponse(ValidationResult):
    requestId: str
    observation: ObservationReceipt
