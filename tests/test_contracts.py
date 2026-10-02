"""Versioned loading, strict payment schema, and explicit API selection."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from drift_guard.api.application import create_app
from drift_guard.config import Settings
from drift_guard.contracts.loader import ContractLoadError, load_contract
from drift_guard.contracts.models import DIALECT
from drift_guard.contracts.repository import BUNDLED_CONTRACTS, FileContractRepository
from tests.test_health import request


def document() -> dict[str, Any]:
    return json.loads((BUNDLED_CONTRACTS / "payments-v1.json").read_text())  # type: ignore[no-any-return]


def write_contract(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "contract.json"
    path.write_text(json.dumps(data))
    return path


def test_hash_ignores_formatting_and_object_order_but_tracks_content(tmp_path: Path) -> None:
    data = document()
    path = write_contract(tmp_path, data)
    original = load_contract(path)
    path.write_text(json.dumps(dict(reversed(list(data.items()))), indent=4))
    assert load_contract(path).content_hash == original.content_hash
    data["schema"]["properties"]["amount"]["minimum"] = 2
    changed = load_contract(write_contract(tmp_path, data))
    assert changed.content_hash != original.content_hash
    assert len(original.content_hash) == 64
    schema = original.schema
    schema["properties"]["amount"]["minimum"] = 900
    assert original.schema["properties"]["amount"]["minimum"] == 1


@pytest.mark.parametrize(
    "schema",
    [
        {"$schema": DIALECT, "type": "invalid-type"},
        {"$schema": DIALECT, "minimum": "sensitive-invalid-value"},
        {"type": "object"},
        {"$schema": "https://json-schema.org/draft-07/schema", "type": "object"},
        {"$schema": DIALECT, "$ref": "https://example.com/external.json"},
        {"$schema": DIALECT, "$ref": "#/$defs/missing"},
        {"$schema": DIALECT, "type": "string", "format": "not-a-supported-format"},
    ],
)
def test_invalid_schema_prevents_startup(tmp_path: Path, schema: dict[str, Any]) -> None:
    data = document()
    data["schema"] = schema
    write_contract(tmp_path, data)
    with pytest.raises(ContractLoadError, match="invalid document or schema") as error:
        create_app(Settings(environment="test", contracts_directory=tmp_path))
    assert "sensitive-invalid-value" not in str(error.value)


@pytest.mark.parametrize("content", ['{"api":', '{"api":"payments","api":"other"}', '{"x":NaN}'])
def test_invalid_json_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(content)
    with pytest.raises(ContractLoadError):
        load_contract(path)


def test_missing_empty_and_duplicate_registry_fail(tmp_path: Path) -> None:
    for directory in (tmp_path, tmp_path / "missing"):
        with pytest.raises(ContractLoadError, match="no JSON contracts"):
            FileContractRepository(directory)
    path = write_contract(tmp_path, document())
    (tmp_path / "duplicate.json").write_text(path.read_text())
    with pytest.raises(ContractLoadError, match="Duplicate contract identity"):
        FileContractRepository(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    [("version", "latest"), ("version", 1), ("direction", "other"), ("operation", "payments")],
)
def test_invalid_identity_rejected(tmp_path: Path, field: str, value: Any) -> None:
    data = document()
    data[field] = value
    with pytest.raises(ContractLoadError):
        load_contract(write_contract(tmp_path, data))


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": "1000"},
        {"amount": True},
        {"amount": 0},
        {"currency": "EUR"},
        {"paymentType": "CASH"},
        {"customerId": ""},
        {"paymentMethod": "CARD"},
    ],
)
def test_payment_fixture_enforces_types_ranges_enums_and_extra_fields(
    changes: dict[str, Any],
) -> None:
    schema = FileContractRepository().get("payments", "v1", "POST /payments", "request").schema
    validator = Draft202012Validator(schema)
    valid = {"customerId": "C123", "amount": 1000, "currency": "INR", "paymentType": "CARD"}
    assert validator.is_valid(valid)
    assert not validator.is_valid({**valid, **changes})
    for field in valid:
        assert not validator.is_valid({key: value for key, value in valid.items() if key != field})


def test_contract_metadata_matches_repository_and_structured_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(Settings(environment="test"))
    repository = app.state.contract_repository
    contract = repository.get("payments", "v1", "POST /payments", "request")
    response = request(
        app,
        "/v1/contracts/payments/v1?operation=POST%20%2Fpayments&direction=request",
        headers={"X-Request-ID": "contract-check"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "contractId": contract.contract_id,
        "api": "payments",
        "version": "v1",
        "operation": "POST /payments",
        "direction": "request",
        "contentHash": contract.content_hash,
        "schemaDialect": DIALECT,
        "requestId": "contract-check",
    }
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    for message in ("contract_loaded", "contract_selected"):
        logged = next(record for record in records if record["message"] == message)
        assert logged["contract_id"] == response.json()["contractId"]
        assert logged["contract_hash"] == response.json()["contentHash"]
    selected = next(record for record in records if record["message"] == "contract_selected")
    assert selected["request_id"] == "contract-check"


def test_multiple_versions_remain_distinct_and_repository_can_be_injected(tmp_path: Path) -> None:
    write_contract(tmp_path, document())
    v2 = document()
    v2["version"] = "v2"
    v2["schema"]["properties"]["currency"]["enum"].append("EUR")
    (tmp_path / "v2.json").write_text(json.dumps(v2))
    repository = FileContractRepository(tmp_path)
    first = repository.get("payments", "v1", "POST /payments", "request")
    second = repository.get("payments", "v2", "POST /payments", "request")
    assert first.content_hash != second.content_hash
    app = create_app(Settings(environment="test"), contract_repository=repository)
    response = request(
        app, "/v1/contracts/payments/v2?operation=POST%20%2Fpayments&direction=request"
    )
    assert response.json()["contentHash"] == second.content_hash


@pytest.mark.parametrize(
    "path",
    [
        "/v1/contracts/payments/v2?operation=POST%20%2Fpayments&direction=request",
        "/v1/contracts/payments/latest?operation=POST%20%2Fpayments&direction=request",
        "/v1/contracts/unknown/v1?operation=POST%20%2Fpayments&direction=request",
        "/v1/contracts/payments/v1?operation=GET%20%2Fpayments&direction=request",
        "/v1/contracts/payments/v1?operation=POST%20%2Fpayments&direction=response",
    ],
)
def test_unknown_selection_never_falls_back(path: str) -> None:
    response = request(create_app(Settings(environment="test")), path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_contract"
    assert response.json()["requestId"] == response.headers["x-request-id"]


@pytest.mark.parametrize(
    "query",
    ["", "?direction=request", "?operation=POST%20%2Fpayments", "?operation=x&direction=other"],
)
def test_required_selection_fields_are_not_defaulted(query: str) -> None:
    response = request(
        create_app(Settings(environment="test")), "/v1/contracts/payments/v1" + query
    )
    assert response.status_code == 422
