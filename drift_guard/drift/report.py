"""Threshold decisions derived only from observed counts."""

from typing import Any

from drift_guard.drift.models import Aggregate, ReportScope, iso_timestamp


def build_report(
    scope: ReportScope,
    aggregate: Aggregate,
    min_samples: int,
    min_count: int,
    min_rate: float,
) -> dict[str, Any]:
    enough = aggregate.samples >= min_samples
    findings = []
    for code in aggregate.codes:
        rate = code.count / aggregate.samples if aggregate.samples else 0
        findings.append(
            {
                "code": code.code,
                "count": code.count,
                "rate": round(rate, 6),
                "firstSeen": iso_timestamp(code.first_seen),
                "lastSeen": iso_timestamp(code.last_seen),
                "thresholdExceeded": enough and code.count >= min_count and rate >= min_rate,
            }
        )
    detected = any(finding["thresholdExceeded"] for finding in findings)
    return {
        "api": scope.api,
        "version": scope.version,
        "operation": scope.operation,
        "direction": scope.direction,
        "environment": scope.environment,
        "contractHash": scope.contract_hash,
        "policyVersion": scope.policy_version,
        "validatorVersion": scope.validator_version,
        "mode": scope.mode,
        "window": {
            "start": iso_timestamp(scope.start),
            "end": iso_timestamp(scope.end),
            "endExclusive": True,
        },
        "sampleCount": aggregate.samples,
        "coverage": "stored_observations_only",
        "invalidCount": aggregate.invalid,
        "firstSeen": iso_timestamp(aggregate.first_seen),
        "lastSeen": iso_timestamp(aggregate.last_seen),
        "status": "insufficient_data"
        if not enough
        else ("threshold_exceeded" if detected else "below_threshold"),
        "driftDetected": detected,
        "thresholds": {"minSamples": min_samples, "minCount": min_count, "minRate": min_rate},
        "findings": findings,
    }
