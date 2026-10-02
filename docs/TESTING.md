# Testing Drift Guard Agent

This guide covers the service currently implemented: health endpoints, request IDs, configuration, and its container setup. API contract validation, drift analysis, and the ADK agent are not implemented yet, so their tests will be added with those features.

## Set up

From the repository root, install the locked development dependencies:

```sh
uv sync --locked --all-groups
```

## Run automated checks

Run the health and request-context tests:

```sh
uv run pytest
```

Run an individual test or see each test name:

```sh
uv run pytest -v tests/test_health.py
```

Run the same code quality checks used by CI:

```sh
uv run ruff check .
uv run mypy drift_guard
uv export --locked --all-groups --no-emit-project --output-file /tmp/drift-guard-audit.lock
uv tool run pip-audit==2.10.1 --requirement /tmp/drift-guard-audit.lock --no-deps --disable-pip --strict
```

## Run the service and smoke test it

Start the local app:

```sh
uv run uvicorn drift_guard.main:app --reload
```

In another terminal, check liveness, readiness, and request ID handling:

```sh
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
curl --fail -i -H 'X-Request-ID: manual-check-1' http://localhost:8000/health/live
```

Expected: HTTP 200; the live response is `{"status":"ok"}`, the ready response includes service and version, and the final response includes `X-Request-ID: manual-check-1`. In local mode, `/docs` and `/openapi.json` should also respond.

## Test the container

With Docker running:

```sh
bash infra/scripts/compose.sh up --build -d
bash infra/scripts/compose.sh ps
curl --fail http://localhost:8080/health/ready
bash infra/scripts/compose.sh logs --tail=100 drift-guard
bash infra/scripts/compose.sh down
```

Compose binds port 8080 to the host's loopback interface. Its health check should show the service as healthy. On EC2, run the same commands over SSH; use `curl http://127.0.0.1:8080/health/ready` on the instance. With `DRIFT_GUARD_ENVIRONMENT=production`, the interactive API docs should be disabled.

## Deploy updates and roll back on your Docker host

From your repository checkout, build an image tagged with the Git commit and deploy it with readiness checks. Replace `agent.example.com` with your own deployment hostname:

```sh
git pull --ff-only
drift_guard_release=$(git rev-parse --short HEAD)
docker build -f infra/Dockerfile --tag "drift-guard-agent:$drift_guard_release" .
bash infra/scripts/release.sh deploy "drift-guard-agent:$drift_guard_release"
curl --fail https://agent.example.com/health/ready
```

The release script records the previously running image ID in the gitignored `.deploy/` directory. A failed rollout automatically attempts to restore that image. To rehearse a manual rollback after a successful deployment:

```sh
bash infra/scripts/release.sh rollback
curl --fail https://agent.example.com/health/ready
bash infra/scripts/compose.sh ps
```

Rollback reuses the exact previous local image without rebuilding it. Keep that image on disk; pruning images can remove your rollback target. Continue using `release.sh` for updates, since plain `bash infra/scripts/compose.sh up` uses the default image unless `DRIFT_GUARD_IMAGE` is set. The script also works for the initial rollout from your existing manually started container.

CI runs `bash infra/scripts/check-rollback.sh` on an isolated runner. It builds two distinct images, deploys both in order, rolls back, checks the restored image ID, and checks readiness. This rehearsal uses port 8080 and should not be run alongside the production service on EC2.

## What the tests cover

- Health routes return the expected status and service version.
- Valid request IDs are preserved; unsafe IDs are replaced.
- Interactive docs and the OpenAPI schema are hidden in production mode.
- Invalid settings are rejected, and environment settings are loaded correctly.
- HTTP failures carry a structured error and request ID; internal errors and validation errors exclude sensitive values from responses.
- CI checks formatting, pytest, Ruff, mypy, dependency vulnerabilities, runtime lock consistency, container health, and image rollback.

When adding features, keep their checks close to the behavior: exact golden cases for deterministic validation, repository tests for persistence/idempotency, and separate ADK evaluations for grounded explanations and safe tool use.
