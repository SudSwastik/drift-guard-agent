"""Versioned rules own severity and decisions, independently of an LLM."""

from collections.abc import Sequence

from drift_guard.validation.models import Decision, Finding, Severity

POLICY_VERSION = "validation-policy-v1"
SEVERITY_RANK: dict[Severity, int] = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def decide(findings: Sequence[Finding], blocking_enabled: bool) -> tuple[Decision, Severity]:
    default: Severity = "NONE"
    severity = max(
        (finding.severity for finding in findings),
        key=lambda value: SEVERITY_RANK[value],
        default=default,
    )
    if severity == "NONE":
        return "ALLOW", severity
    if blocking_enabled and severity in ("MEDIUM", "HIGH"):
        return "BLOCK", severity
    return "WARN", severity
