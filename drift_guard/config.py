"""Validated service configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables prefixed with ``DRIFT_GUARD_``."""

    model_config = SettingsConfigDict(
        env_prefix="DRIFT_GUARD_",
        env_file=None,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    service_name: str = Field(default="drift-guard-agent", min_length=1, max_length=63)
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    contracts_directory: Path | None = None
    api_key: SecretStr | None = Field(default=None, min_length=32, max_length=256)
    blocking_enabled: bool = False
    max_request_bytes: int = Field(default=65536, ge=256, le=1048576)
    max_json_depth: int = Field(default=32, ge=2, le=64)
    max_findings: int = Field(default=100, ge=1, le=500)
    validation_timeout_seconds: float = Field(default=5, ge=0.1, le=30)
    validation_concurrency: int = Field(default=2, ge=1, le=8)

    @field_validator("api_key", mode="before")
    @classmethod
    def normalize_empty_api_key(cls, value: Any) -> Any:
        return None if value == "" else value

    @model_validator(mode="after")
    def require_production_authentication(self) -> "Settings":
        if self.environment in ("staging", "production") and self.api_key is None:
            raise ValueError(
                "Staging/production requires DRIFT_GUARD_API_KEY (at least 32 characters)"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings, parsing and validating them once."""
    return Settings()
