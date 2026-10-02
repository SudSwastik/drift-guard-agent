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
docker compose up --build -d
docker compose ps
curl --fail http://localhost:8080/health/ready
docker compose logs --tail=100 drift-guard
docker compose down
```

Compose binds port 8080 to the host's loopback interface. Its health check should show the service as healthy. On EC2, run the same commands over SSH; use `curl http://127.0.0.1:8080/health/ready` on the instance. With `DRIFT_GUARD_ENVIRONMENT=production`, the interactive API docs should be disabled.

## What the tests cover

- Health routes return the expected status and service version.
- Valid request IDs are preserved; unsafe IDs are replaced.
- Interactive docs and the OpenAPI schema are hidden in production mode.
- CI runs pytest, Ruff, mypy, and a Docker image build on pushes and pull requests.

When adding features, keep their checks close to the behavior: exact golden cases for deterministic validation, repository tests for persistence/idempotency, and separate ADK evaluations for grounded explanations and safe tool use.
