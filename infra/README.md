# Infrastructure

Docker, Compose, deployment scripts, and CI checks live here. Run commands from the repository root:

```sh
cp infra/.env.example .env
python3 infra/scripts/configure-env.py
bash infra/scripts/compose.sh up -d --build
bash infra/scripts/compose.sh ps
bash infra/scripts/compose.sh logs --tail=100 drift-guard
```

For an existing `.env`, run the generator without copying the example over it. It fills missing API/database/HMAC secrets, preserves existing values, and writes with permissions 600 without printing secrets.

The wrapper keeps `.env` at the repository root and preserves the Compose project name. For production, set `DRIFT_GUARD_ENVIRONMENT=production` in the ignored `.env`. Callers send the API key in `X-API-Key`; health checks remain public. Keep `DRIFT_GUARD_BLOCKING_ENABLED=false` initially.

Compose runs PostgreSQL privately, with no host database port exposed. Its named `postgres-data` volume survives application rebuilds and rollback. The application role owns its database but cannot create roles/databases or act as a superuser. The API container receives the application password; the admin password stays in the database service. Initialization SQL runs only for a fresh volume.

The API container has a 256 MiB/64-process limit and defaults to two validation slots. PostgreSQL has a 512 MiB/128-process limit. Run authenticated smoke checks with environment values loaded from `.env`:

```sh
uv run --env-file .env python infra/scripts/smoke-validation.py http://localhost:8080
```

Build and deploy a release, or restore its previous image:

```sh
docker build -f infra/Dockerfile -t drift-guard-agent:RELEASE .
bash infra/scripts/release.sh deploy drift-guard-agent:RELEASE
bash infra/scripts/release.sh rollback
```

Release updates recreate the application and preserve the running database. Do not use `compose down --volumes` on a deployment: it deletes stored observations. Changing a password in `.env` does not rotate an existing PostgreSQL role; update the role securely before recreating containers. Keep the HMAC key stable for the retention window so retries continue to deduplicate.

To run local Uvicorn against the bundled database, expose PostgreSQL only on loopback with the development overlay:

```sh
docker compose --project-directory . -f infra/compose.yaml -f infra/compose.local.yaml up -d --wait postgres
uv run uvicorn drift_guard.main:app --env-file .env --reload
```

Use `DRIFT_GUARD_DATABASE_URL=postgresql+asyncpg://...` in the ignored environment for an external PostgreSQL server. Application/maintenance commands also accept `DRIFT_GUARD_POSTGRES_HOST`, `DRIFT_GUARD_POSTGRES_PORT`, `DRIFT_GUARD_POSTGRES_USER`, `DRIFT_GUARD_POSTGRES_DATABASE`, and `DRIFT_GUARD_POSTGRES_PASSWORD`; defaults match the bundled database.

CI runs PostgreSQL repository checks and a Docker deployment/rollback/persistence/outage rehearsal. GitHub's entry point remains in `.github/workflows/ci.yml`. Nginx configuration remains on the deployment host. Detailed test and maintenance commands are in `docs/TESTING.md`.
