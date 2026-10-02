"""Validated service configuration."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings loaded from environment variables prefixed with ``DRIFT_GUARD_``."""

    model_config = SettingsConfigDict(
        env_prefix="DRIFT_GUARD_",
        env_file=None,
        extra="forbid",
        frozen=True,
    )

    service_name: str = Field(default="drift-guard-agent", min_length=1, max_length=63)
    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings, parsing and validating them once."""
    return Settings()
