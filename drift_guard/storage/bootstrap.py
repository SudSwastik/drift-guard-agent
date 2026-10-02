"""Local persistence defaults and stable private keys for request fingerprints."""

import os
import secrets
from pathlib import Path

from sqlalchemy.engine import URL, make_url

from drift_guard.config import Settings
from drift_guard.drift.service import ObservationService
from drift_guard.storage.repository import ObservationRepository, SQLObservationRepository


def _local_key(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        key = path.read_bytes()
    else:
        key = secrets.token_bytes(32)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(key)
    if len(key) != 32:
        raise ValueError("Local observation fingerprint key must contain 32 bytes")
    return key


def observation_service(
    settings: Settings, repository: ObservationRepository | None = None
) -> ObservationService:
    configured = settings.database_url
    if configured is not None:
        database_url = configured.get_secret_value()
    elif settings.environment == "test" and settings.postgres_password is None:
        database_url = "sqlite+aiosqlite:///:memory:"
    else:
        database_url = URL.create(
            "postgresql+asyncpg",
            username=settings.postgres_user,
            password=settings.postgres_password.get_secret_value()
            if settings.postgres_password
            else None,
            host=settings.postgres_host,
            port=settings.postgres_port,
            database=settings.postgres_database,
        ).render_as_string(hide_password=False)
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+asyncpg"
        and settings.environment != "test"
        and repository is None
    ):
        raise ValueError("Application deployments require PostgreSQL storage")
    if settings.observation_hmac_key is not None:
        key = settings.observation_hmac_key.get_secret_value().encode("utf-8")
    elif url.drivername == "sqlite+aiosqlite" and url.database not in (None, "", ":memory:"):
        key = _local_key(Path(url.database).parent / "observation-hmac.key")
    elif settings.api_key is not None:
        key = settings.api_key.get_secret_value().encode("utf-8")
    elif url.drivername == "postgresql+asyncpg":
        key = _local_key(Path(".state/observation-hmac.key"))
    else:
        key = secrets.token_bytes(32)
    return ObservationService(
        repository if repository is not None else SQLObservationRepository(database_url),
        settings,
        key,
    )
