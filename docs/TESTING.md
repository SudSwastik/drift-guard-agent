# Testing Drift Guard Agent

This guide covers health endpoints, request IDs, configuration, versioned API contracts, deterministic payload validation, and the container setup. Drift persistence/analysis and the ADK agent follow in later features.

## Set up

From the repository root, install the locked development dependencies:

```sh
uv sync --locked --all-groups
```

## Run automated checks

Run all automated tests:

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

## Test versioned contracts (Feature 2)

With the local server running, select the exact API, version, operation, and direction:

```sh
curl --fail --get http://localhost:8000/v1/contracts/payments/v1 \
  --data-urlencode 'operation=POST /payments' \
  --data-urlencode 'direction=request' \
  -H 'X-Request-ID: contract-check-1'
```

Expected: HTTP 200 with `contractId`, `contentHash` (64 hexadecimal characters), `schemaDialect`, and `requestId`. Repeating the selection returns the same ID/hash. Logs contain `contract_loaded` at startup and `contract_selected` on access, both with the matching ID/hash. This endpoint reports contract metadata. Use `/v1/validate` below to check a payload.

Replace `v1` with `v2` or `latest`: expect HTTP 404 with `error.code` equal to `unknown_contract`. Omitting operation or direction returns HTTP 422. No selection falls back to another version.

The bundled fixture is `drift_guard/contracts/fixtures/payments-v1.json`. It defines required fields, strict numeric amounts, a minimum of 1, allowed currencies/payment types, and rejects additional fields. Contracts use JSON Schema draft 2020-12. Inline schemas are supported; `$ref`/`$dynamicRef` references and formats without an installed checker are rejected at startup.

To configure a different directory of trusted contract JSON files:

```sh
DRIFT_GUARD_CONTRACTS_DIRECTORY=/path/to/contracts uv run uvicorn drift_guard.main:app
```

The directory replaces the bundled fixtures. It must contain at least one `*.json` file; every file must have a valid envelope/schema and a unique API/version/operation/direction identity. Invalid JSON, duplicate JSON keys, unsupported dialects/references, malformed schemas, and duplicate identities stop startup. Files are loaded once; restart to load changes. The SHA-256 hash covers the entire normalized envelope, ignoring whitespace/object key order while preserving array order.

Run the contract-specific automated cases:

```sh
uv run pytest -v tests/test_contracts.py
```

## Test payload validation (Feature 3)

With the local server running, submit a valid payment:

```sh
curl --fail http://localhost:8000/v1/validate \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: payment-check-1' \
  -d '{"api":"payments","version":"v1","operation":"POST /payments","direction":"request","payload":{"customerId":"C123","amount":1000,"currency":"INR","paymentType":"CARD"}}'
```

Expected: HTTP 200, `valid: true`, `decision: ALLOW`, `severity: NONE`, and empty `findings`. Results identify the exact contract hash, validator version, policy version, mode, and request ID.

Now submit the original drift example:

```sh
curl --fail http://localhost:8000/v1/validate \
  -H 'Content-Type: application/json' \
  -d '{"api":"payments","version":"v1","operation":"POST /payments","direction":"request","payload":{"customerId":"C123","amount":"1000","currency":"INR","paymentMethod":"CARD"}}'
```

Expected: HTTP 200, `valid: false`, `decision: WARN`, `severity: HIGH`, and these findings in stable order:

| Code | Path | Severity |
| --- | --- | --- |
| `invalid_type` | `/amount` | HIGH |
| `unexpected_field` | `/paymentMethod` | LOW |
| `missing_required_field` | `/paymentType` | HIGH |

Paths use JSON Pointer, relative to the submitted payload. The empty string means the root; `/` and `~` in field names are escaped as `~1` and `~0`. Findings contain fixed messages without payload values. Optional context accepts only `environment` and `clientId`; it does not affect policy or authorization and is not logged.

Policy `validation-policy-v1`: missing fields, wrong types, and unclassified schema violations are HIGH; enums, ranges, lengths, patterns, and formats are MEDIUM; extra fields are LOW. Default `report_only` mode returns WARN for any violation. With `DRIFT_GUARD_BLOCKING_ENABLED=true`, MEDIUM/HIGH violations return BLOCK while LOW violations still return WARN. ALLOW means no violations. BLOCK is a recommendation in the response; this service does not forward or reject a downstream payment itself.

Restart with enforcement enabled to test BLOCK:

```sh
DRIFT_GUARD_BLOCKING_ENABLED=true uv run uvicorn drift_guard.main:app --reload
```

Run the golden cases against a running service:

```sh
python3 infra/scripts/smoke-validation.py http://localhost:8000
# If enforcement is enabled:
python3 infra/scripts/smoke-validation.py http://localhost:8000 --mode enforce
```

Or run automated validation/policy/API tests:

```sh
uv run pytest -v tests/test_validation.py tests/test_validation_api.py
```

### Authentication and request limits

Local/test mode permits development without a key. When `DRIFT_GUARD_API_KEY` is set, both contracts and validation endpoints require `X-API-Key`. Staging/production refuses startup without a key of at least 32 characters. Health endpoints remain available for probes.

Generate a key and start an authenticated local server:

```sh
export DRIFT_GUARD_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
uv run uvicorn drift_guard.main:app --reload
```

Use `-H "X-API-Key: $DRIFT_GUARD_API_KEY"` on curl requests. Export the same variable in the terminal running the smoke script. In Swagger, click **Authorize** and enter your key before executing protected endpoints. The app reads environment variables directly; local Uvicorn does not automatically load the root `.env` file.

Service failures are separate from completed validation results:

| HTTP | Meaning |
| --- | --- |
| 200 | Validation completed, including WARN/BLOCK results |
| 400 | Malformed JSON, duplicate keys, invalid/non-finite numbers, or invalid Content-Length |
| 401 | Missing/incorrect configured API key |
| 404 | Unknown API/version/operation/direction selection |
| 413 | Body exceeds byte/depth limits |
| 415 | Content type is not application/json, or body is compressed |
| 422 | Invalid validation envelope/context |
| 429 | All validation slots are busy; Retry-After is 1 second |
| 503 | Validation worker/repository failure; no verdict is issued |
| 504 | Body read/worker exceeds its time budget; no verdict is issued |

Defaults: 65,536 bytes for the full envelope, 32 nested JSON containers (including the envelope), 100 findings, 5 seconds per validation request, and 2 active validation slots per application process. Configure via `DRIFT_GUARD_MAX_REQUEST_BYTES`, `DRIFT_GUARD_MAX_JSON_DEPTH`, `DRIFT_GUARD_MAX_FINDINGS`, `DRIFT_GUARD_VALIDATION_TIMEOUT_SECONDS`, and `DRIFT_GUARD_VALIDATION_CONCURRENCY`. Busy requests are rejected rather than queued. CPU validation runs in a separate cancellable process; timed-out workers are killed. When findings exceed the limit, `findingsTruncated` is true and a HIGH severity limit finding prevents a false ALLOW.

## Test the container

With Docker running:

```sh
bash infra/scripts/compose.sh up --build -d
bash infra/scripts/compose.sh ps
curl --fail http://localhost:8080/health/ready
curl --fail --get http://localhost:8080/v1/contracts/payments/v1 \
  --data-urlencode 'operation=POST /payments' --data-urlencode 'direction=request'
python3 infra/scripts/smoke-validation.py http://localhost:8080
bash infra/scripts/compose.sh logs --tail=100 drift-guard
bash infra/scripts/compose.sh down
```

Compose binds port 8080 to the host's loopback interface. Its health check should show the service as healthy. On EC2, run the same commands over SSH; use `curl http://127.0.0.1:8080/health/ready` on the instance. With `DRIFT_GUARD_ENVIRONMENT=production`, the interactive API docs should be disabled.

## Deploy updates and roll back on your Docker host

Before deploying this feature to production, add a generated `DRIFT_GUARD_API_KEY` to your ignored root `.env` file. Keep `DRIFT_GUARD_BLOCKING_ENABLED=false` for the initial rollout. Without a key, the new production application intentionally fails startup. Compose passes these settings to the container; API callers must now send the key. Export the same key when running the smoke script on the host.

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

CI runs `bash infra/scripts/check-rollback.sh` on an isolated runner. It generates a temporary API key, builds two distinct images, deploys both in order, runs validation golden cases, rolls back, checks the restored image ID/readiness, and runs validation again. This rehearsal uses port 8080 and should not be run alongside the production service on EC2.

## What the tests cover

- Health routes return the expected status and service version.
- Valid request IDs are preserved; unsafe IDs are replaced.
- Interactive docs and the OpenAPI schema are hidden in production mode.
- Invalid settings are rejected, and environment settings are loaded correctly.
- Contract loading validates schemas/identities, preserves stable content hashes, and fails startup on invalid or duplicate contracts.
- Contract selection requires every identity field and reports unknown selections without version fallback.
- HTTP failures carry a structured error and request ID; internal errors and validation errors exclude sensitive values from responses.
- Golden payloads assert exact finding codes/paths/severity and both policy modes; reordered inputs produce identical results.
- Validation API checks real worker execution, authentication, byte/depth limits, malformed input, formats, privacy, capacity saturation, worker outage, and deadline cancellation/recovery.
- CI checks formatting, pytest, Ruff, mypy, dependency vulnerabilities, runtime lock consistency, container health, and image rollback.

When adding features, keep their checks close to the behavior: exact golden cases for deterministic validation, repository tests for persistence/idempotency, and separate ADK evaluations for grounded explanations and safe tool use.
