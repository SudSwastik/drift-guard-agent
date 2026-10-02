# Testing Drift Guard Agent

This guide covers the service foundation, versioned contracts, deterministic validation, PostgreSQL observation storage, and threshold-based contract drift reports. The ADK investigation agent follows in a later feature.

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

For a new checkout, copy `infra/.env.example` to `.env`. Keep any existing `.env`. Generate only missing secrets:

```sh
python3 infra/scripts/configure-env.py
```

This fills API, PostgreSQL app/admin, and HMAC keys in the ignored file without printing their values. Existing settings/secrets are preserved.

Start the bundled database with loopback access for local Uvicorn:

```sh
docker compose --project-directory . -f infra/compose.yaml -f infra/compose.local.yaml up -d --wait postgres
uv run uvicorn drift_guard.main:app --env-file .env --reload
```

In another terminal, check liveness, readiness, and request ID handling:

```sh
curl --fail http://localhost:8000/health/live
curl --fail http://localhost:8000/health/ready
curl --fail -i -H 'X-Request-ID: manual-check-1' http://localhost:8000/health/live
```

Expected: HTTP 200; the live response is `{"status":"ok"}`, the ready response includes service and version, and the final response includes `X-Request-ID: manual-check-1`. In local mode, `/docs` and `/openapi.json` should also respond.

The generated `.env` configures an API key even in local mode. Use Swagger's **Authorize** button or add `-H "X-API-Key: $DRIFT_GUARD_API_KEY"` to protected curl examples after exporting your configured key. For smoke scripts, `uv run --env-file .env` loads it directly. Health probes do not need a key.

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
DRIFT_GUARD_BLOCKING_ENABLED=true uv run uvicorn drift_guard.main:app --env-file .env --reload
```

Run the golden cases against a running service:

```sh
uv run --env-file .env python infra/scripts/smoke-validation.py http://localhost:8000
# If enforcement is enabled:
uv run --env-file .env python infra/scripts/smoke-validation.py http://localhost:8000 --mode enforce
```

Or run automated validation/policy/API tests:

```sh
uv run pytest -v tests/test_validation.py tests/test_validation_api.py
```

### Authentication and request limits

Local/test mode permits development without a key. When `DRIFT_GUARD_API_KEY` is set, contracts, validation, drift, and observation-status endpoints require `X-API-Key`. Staging/production refuses startup without a key of at least 32 characters. Health endpoints remain available for probes.

Generate a key and start an authenticated local server:

```sh
export DRIFT_GUARD_API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
uv run uvicorn drift_guard.main:app --reload
```

Use `-H "X-API-Key: $DRIFT_GUARD_API_KEY"` on curl requests. Export the same variable in the terminal running the smoke script. In Swagger, click **Authorize** and enter your key before executing protected endpoints. The app reads environment variables directly; pass `--env-file .env` to local Uvicorn to load the generated configuration.

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

## Test observations and contract drift (Feature 4)

Every completed validation attempts to store an observation and returns:

```json
"observation": {"status": "stored", "id": "generated-observation-id"}
```

Send `Idempotency-Key: payment-test-1` with a validation request. Repeat the same request/key: expect `status: duplicate`, the same observation ID, and no increase in report counts. Changing the payload/context, contract hash, or validation result with the same key returns HTTP 409 `idempotency_conflict`. Keys are scoped to the selected contract/environment. Without a key, each call is a new observation. Deduplication expires when retention deletes the observation.

Retrieve a report in Swagger or with curl:

```sh
curl --fail --get http://localhost:8000/v1/drift/payments/v1 \
  --data-urlencode 'operation=POST /payments' \
  --data-urlencode 'direction=request' \
  --data-urlencode 'windowSeconds=3600' \
  -H "X-API-Key: $DRIFT_GUARD_API_KEY"
```

Default thresholds require at least 10 stored samples, at least 3 observations with a finding code, and that code appearing in at least 20% of samples. A single error returns `insufficient_data`, not a detected trend. Reports include total/invalid counts, each code's count/rate/first/last seen, thresholds, and `driftDetected`. A code counts once per observation even when it appears at multiple JSON paths.

Report scopes include the API, version, operation, direction, service environment, current contract hash, policy version, validator version, and enforcement mode. Different rule sets/modes are kept separate. Optional `start`/`end` must both be timezone-aware timestamps; windows are `[start, end)`, at most 7 days, within retention, and no later than the server clock. `context.environment` does not relabel the service environment. These reports describe repeated contract violations; they do not infer causes or measure valid-value distribution drift.

To generate a reproducible test batch in a quiet test environment:

```sh
uv run --env-file .env python infra/scripts/smoke-drift.py http://localhost:8000 seed \
  --snapshot-file /tmp/drift-guard-report.json
uv run --env-file .env python infra/scripts/smoke-drift.py http://localhost:8000 verify \
  --snapshot-file /tmp/drift-guard-report.json
```

The seed adds 10 unique samples (3 invalid and 7 valid). Verify replays one event and checks unchanged counts. You can restart the application between these commands to check persistence/idempotency. Avoid concurrent test traffic during exact-count checks.

Inspect storage readiness and per-process counters:

```sh
curl --fail http://localhost:8000/v1/observations/status -H "X-API-Key: $DRIFT_GUARD_API_KEY"
```

Only metadata is persisted in `guard_observations` and `guard_observation_codes`: timestamps, contract/rule identities, decisions/severity, unique finding codes, and keyed HMAC fingerprints. Payloads, field paths/names, context, request IDs, API keys, and raw idempotency keys are omitted. The fingerprint key must stay stable across restarts and credential rotation. Status counters reset when the application process restarts; report counts come from PostgreSQL.

### Storage outages and retention

`DRIFT_GUARD_OBSERVATION_FAILURE_MODE=best_effort` is the default. Validation still returns its deterministic verdict when storage fails, with `observation.status: unavailable`. Failed writes are logged without database details and counted in `writeFailures`. Lost observations are not queued/backfilled; retry with the same idempotency key after recovery to record them safely. Drift rates cover stored observations only, so an outage can reduce coverage.

With `required`, a storage failure returns HTTP 503 without a verdict, readiness returns 503, and startup fails if storage is unavailable. Drift queries return 503 on storage failure in either mode. Body/CPU validation has its existing budget; persistence has a separate default 2-second budget. Configure it with `DRIFT_GUARD_STORAGE_TIMEOUT_SECONDS`.

Retention defaults to 14 days. Startup and the background task remove up to 1,000 expired observations per batch, with cascading finding-code deletion. The background task runs every 600 seconds; configure `DRIFT_GUARD_OBSERVATION_RETENTION_DAYS` and `DRIFT_GUARD_RETENTION_INTERVAL_SECONDS`. A large backlog can take several batches; the explicit purge command removes all expired batches. Reports cannot query outside retention.

Schema version 1 is initialized transactionally at startup. Unknown schema versions prevent startup; they are not silently upgraded/downgraded. Explicit maintenance commands:

```sh
uv run --env-file .env python -m drift_guard.storage.maintenance migrate
uv run --env-file .env python -m drift_guard.storage.maintenance purge
# On the Docker deployment:
bash infra/scripts/compose.sh exec -T drift-guard python -m drift_guard.storage.maintenance purge
```

Use PostgreSQL backups before future schema upgrades. Application rollback preserves the database and does not reverse migrations. For a manual backup using the bundled database:

```sh
umask 077
bash infra/scripts/compose.sh exec -T postgres pg_dump -U postgres -d drift_guard -Fc > /tmp/drift-guard-observations.dump
```

Store the backup securely outside the container; include the stable HMAC key in your secret recovery procedure. Do not run `compose down --volumes` on a deployment, since it deletes the observation database.

### Automated PostgreSQL and outage checks

```sh
uv run pytest -v tests/test_storage.py tests/test_drift.py tests/test_observation_api.py
```

Fast isolated checks use test-only databases. CI also runs the same repository contract against PostgreSQL. To run those integration cases locally, point this variable at a dedicated test server with permission to create/drop temporary databases:

```sh
DRIFT_GUARD_TEST_POSTGRES_URL='postgresql+asyncpg://postgres:<test-password>@127.0.0.1:5432/postgres' \
  uv run pytest -v tests/test_storage.py
```

`bash infra/scripts/check-rollback.sh` uses an isolated Compose project: PostgreSQL migration, authenticated validation, exact drift counts, application rollback, persistent idempotency, database shutdown/outage signals, and recovery. Its generated test volume is removed at the end. It uses port 8080; run it on an isolated Docker host, not beside the deployment.

## Test the container

With Docker running:

```sh
bash infra/scripts/compose.sh up --build -d
bash infra/scripts/compose.sh ps
curl --fail http://localhost:8080/health/ready
curl --fail --get http://localhost:8080/v1/contracts/payments/v1 \
  --data-urlencode 'operation=POST /payments' --data-urlencode 'direction=request'
uv run --env-file .env python infra/scripts/smoke-validation.py http://localhost:8080
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

CI runs `bash infra/scripts/check-rollback.sh` on an isolated runner. It generates temporary API/database/HMAC credentials, builds two distinct images, deploys both in order, validates payloads, seeds observations, rolls back, checks persisted counts/idempotency, takes PostgreSQL offline, and checks recovery. The running database is preserved across application releases. This rehearsal uses port 8080 and should not be run alongside the production service.

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
- PostgreSQL repository tests cover migrations, concurrent idempotency, transaction rollback, exact scope/window filtering, retention/cascades, and persistence. Drift tests use fixed clocks for thresholds and boundaries.
- CI checks formatting, pytest, Ruff, mypy, dependency vulnerabilities, runtime lock consistency, container health, and image rollback.

When adding features, keep their checks close to the behavior: exact golden cases for deterministic validation, repository tests for persistence/idempotency, and separate ADK evaluations for grounded explanations and safe tool use.
