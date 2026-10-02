# Infrastructure

Docker, Compose, deployment scripts, and CI checks live here. Run commands from the repository root:

```sh
cp infra/.env.example .env
bash infra/scripts/compose.sh up -d --build
bash infra/scripts/compose.sh ps
bash infra/scripts/compose.sh logs --tail=100 drift-guard
```

The wrapper keeps `.env` at the repository root and preserves the `drift-guard-agent` Compose project name used on EC2. For production, set `DRIFT_GUARD_ENVIRONMENT=production` and a generated `DRIFT_GUARD_API_KEY` (at least 32 characters) in the ignored `.env` file. The app will not start in production without the key. Callers send it in `X-API-Key`; health checks remain public. Keep `DRIFT_GUARD_BLOCKING_ENABLED=false` for report-only validation initially.

Generate a key with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`. Keep the generated value outside Git. Compose limits the container to 256 MiB/64 processes, and defaults to two validation slots. Export the configured key to run the API smoke check from the host:

```sh
python3 infra/scripts/smoke-validation.py http://localhost:8080
```

Build and deploy a release, or restore its previous image:

```sh
docker build -f infra/Dockerfile -t drift-guard-agent:RELEASE .
bash infra/scripts/release.sh deploy drift-guard-agent:RELEASE
bash infra/scripts/release.sh rollback
```

CI runs `infra/ci/checks.sh` and `infra/scripts/check-rollback.sh`. GitHub's workflow entry point remains in `.github/workflows/ci.yml`. Nginx configuration remains on the EC2 host.
