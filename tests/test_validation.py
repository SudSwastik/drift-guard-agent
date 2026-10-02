"""Golden deterministic findings, policy, repeatability, and schema keyword handling."""

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from drift_guard.contracts.loader import load_contract
from drift_guard.contracts.models import DIALECT, Contract
from drift_guard.contracts.repository import FileContractRepository
from drift_guard.validation.engine import VALIDATOR_VERSION, validate_payload
from drift_guard.validation.policy import POLICY_VERSION

CASES = json.loads((Path(__file__).parent / "fixtures" / "validation-cases.json").read_text())


@pytest.fixture
def payment_contract() -> Contract:
    return FileContractRepository().get("payments", "v1", "POST /payments", "request")


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
@pytest.mark.parametrize("blocking", [False, True])
def test_golden_findings_and_policy(
    payment_contract: Contract, case: dict[str, Any], blocking: bool
) -> None:
    result = validate_payload(payment_contract, case["payload"], blocking, 100)
    assert [[finding.code, finding.path] for finding in result.findings] == case["findings"]
    assert result.severity == case["severity"]
    assert result.valid is (not case["findings"])
    expected = "ALLOW" if result.valid else "WARN"
    if blocking and result.severity in ("MEDIUM", "HIGH"):
        expected = "BLOCK"
    assert result.decision == expected
    assert result.validatorVersion == VALIDATOR_VERSION
    assert result.policyVersion == POLICY_VERSION
    assert result.contractHash == payment_contract.content_hash
    assert "private-value" not in result.model_dump_json()
    assert not result.findingsTruncated


def test_repeatability_and_input_order_do_not_change_findings(payment_contract: Contract) -> None:
    payload = CASES[1]["payload"]
    expected = validate_payload(payment_contract, payload, False, 100)
    for ordered in itertools.permutations(payload.items()):
        assert validate_payload(payment_contract, dict(ordered), False, 100) == expected


def custom_contract(tmp_path: Path, schema: dict[str, Any]) -> Contract:
    path = tmp_path / "custom.json"
    path.write_text(
        json.dumps(
            {
                "api": "example",
                "version": "v1",
                "operation": "POST /example",
                "direction": "request",
                "schema": {"$schema": DIALECT, **schema},
            }
        )
    )
    return load_contract(path)


def test_nested_paths_format_range_and_json_pointer_escaping(tmp_path: Path) -> None:
    contract = custom_contract(
        tmp_path,
        {
            "type": "object",
            "properties": {
                "a/b~c": {"type": "array", "items": {"type": "string", "format": "email"}},
                "amount": {"type": "number", "maximum": 10},
            },
        },
    )
    result = validate_payload(contract, {"a/b~c": ["private-email"], "amount": 11}, False, 100)
    assert [(finding.code, finding.path) for finding in result.findings] == [
        ("above_maximum", "/amount"),
        ("invalid_format", "/a~1b~0c/0"),
    ]
    assert "private-email" not in result.model_dump_json()


def test_pattern_properties_are_not_reported_as_extra_fields(tmp_path: Path) -> None:
    contract = custom_contract(
        tmp_path,
        {
            "type": "object",
            "patternProperties": {"^allowed_": {"type": "number"}},
            "additionalProperties": False,
        },
    )
    result = validate_payload(contract, {"allowed_one": 1, "extra": "secret"}, False, 100)
    assert [(finding.code, finding.path) for finding in result.findings] == [
        ("unexpected_field", "/extra")
    ]


def test_unmapped_schema_keyword_is_a_high_severity_violation(tmp_path: Path) -> None:
    contract = custom_contract(tmp_path, {"not": {"type": "string"}})
    result = validate_payload(contract, "secret", True, 100)
    assert result.decision == "BLOCK"
    assert result.findings[0].code == "schema_violation"


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_finding_limits_never_produce_a_false_allow(payment_contract: Contract, limit: int) -> None:
    result = validate_payload(payment_contract, {}, True, limit)
    assert len(result.findings) == limit
    assert result.findingsTruncated
    assert result.findings[-1].code == "finding_limit_exceeded"
    assert result.severity == "HIGH" and result.decision == "BLOCK"


def test_exact_finding_limit_is_not_truncated(payment_contract: Contract) -> None:
    result = validate_payload(payment_contract, {}, False, 4)
    assert len(result.findings) == 4
    assert not result.findingsTruncated
