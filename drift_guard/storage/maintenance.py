"""Explicit schema initialization and complete retention cleanup commands."""

import argparse
import json
from datetime import timedelta

import anyio

from drift_guard.config import get_settings
from drift_guard.drift.models import timestamp, utc_now
from drift_guard.storage.bootstrap import observation_service
from drift_guard.storage.schema import SCHEMA_VERSION


async def maintain(command: str) -> None:
    service = observation_service(get_settings())
    deleted = 0
    try:
        with anyio.fail_after(service.settings.storage_timeout_seconds):
            await service.repository.initialize()
        if command == "purge":
            cutoff = timestamp(
                utc_now() - timedelta(days=service.settings.observation_retention_days)
            )
            while True:
                with anyio.fail_after(service.settings.storage_timeout_seconds):
                    count = await service.repository.purge(cutoff)
                deleted += count
                if count == 0:
                    break
    except Exception:
        raise SystemExit(
            "Database maintenance failed; check storage availability and schema version."
        ) from None
    finally:
        await service.repository.close()
    print(json.dumps({"schemaVersion": SCHEMA_VERSION, "deleted": deleted}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["migrate", "purge"])
    args = parser.parse_args()
    anyio.run(maintain, args.command)


if __name__ == "__main__":
    main()
