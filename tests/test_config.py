"""Configuration failures must prevent a service from starting incorrectly."""

import pytest
from pydantic import SecretStr, ValidationError

from drift_guard.config import Settings


def test_configuration_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRIFT_GUARD_API_KEY", "test-only-api-key-32-characters-long")
    monkeypatch.setenv("DRIFT_GUARD_ENVIRONMENT", "staging")
    monkeypatch.setenv("DRIFT_GUARD_SERVICE_NAME", "staging-guard")
    settings = Settings()
    assert settings.environment == "staging"
    assert settings.service_name == "staging-guard"


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("DRIFT_GUARD_ENVIRONMENT", "unknown"),
        ("DRIFT_GUARD_LOG_LEVEL", "invalid"),
        ("DRIFT_GUARD_SERVICE_NAME", ""),
        ("DRIFT_GUARD_SERVICE_NAME", "a" * 64),
    ],
)
def test_invalid_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    monkeypatch.setenv(variable, value)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_hosted_environment_requires_api_key(
    environment: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DRIFT_GUARD_ENVIRONMENT", environment)
    monkeypatch.delenv("DRIFT_GUARD_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="requires DRIFT_GUARD_API_KEY"):
        Settings()


def test_empty_api_key_is_only_allowed_for_local_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DRIFT_GUARD_API_KEY", "")
    assert Settings(environment="test").api_key is None
    with pytest.raises(ValidationError):
        Settings(environment="production")


@pytest.mark.parametrize(
    "changes",
    [
        {"max_request_bytes": 0},
        {"max_json_depth": 100},
        {"max_findings": 0},
        {"validation_timeout_seconds": 0},
        {"validation_concurrency": 0},
        {"api_key": SecretStr("short-private-key")},
    ],
)
def test_invalid_validation_configuration_fails(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as error:
        Settings.model_validate({"environment": "test", **changes})
    assert "short-private-key" not in str(error.value)
