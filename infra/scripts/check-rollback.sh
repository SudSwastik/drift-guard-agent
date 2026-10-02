#!/usr/bin/env bash
# Run only on an isolated Docker host/project; never against the production project.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
docker_compose() {
    bash "$project_dir/infra/scripts/compose.sh" "$@"
}
export COMPOSE_PROJECT_NAME="drift-guard-rehearsal-$$"
export DRIFT_GUARD_ENVIRONMENT=production
export DRIFT_GUARD_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export DRIFT_GUARD_POSTGRES_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
export DRIFT_GUARD_POSTGRES_ADMIN_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
export DRIFT_GUARD_OBSERVATION_HMAC_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export DRIFT_GUARD_DATABASE_URL=""
export DRIFT_GUARD_OBSERVATION_FAILURE_MODE=required
export DRIFT_GUARD_BLOCKING_ENABLED=false
export DRIFT_GUARD_STATE_DIR="$(mktemp -d)"
cleanup() {
    docker_compose down --volumes --remove-orphans
    rm -rf "$DRIFT_GUARD_STATE_DIR"
}
trap cleanup EXIT

# Different labels guarantee distinct images even when rehearsing the same source.
docker build --file infra/Dockerfile --label rehearsal=baseline --tag drift-guard-agent:baseline .
docker build --file infra/Dockerfile --label rehearsal=candidate --tag drift-guard-agent:candidate .
bash infra/scripts/release.sh deploy drift-guard-agent:baseline
baseline="$(docker image inspect --format '{{.Id}}' drift-guard-agent:baseline)"
bash infra/scripts/release.sh deploy drift-guard-agent:candidate
python3 infra/scripts/smoke-validation.py http://127.0.0.1:8080
python3 infra/scripts/smoke-drift.py http://127.0.0.1:8080 seed --snapshot-file "$DRIFT_GUARD_STATE_DIR/drift.json"
candidate="$(docker image inspect --format '{{.Id}}' drift-guard-agent:candidate)"
[[ "$baseline" != "$candidate" ]]
bash infra/scripts/release.sh rollback
container_id="$(docker_compose ps --quiet drift-guard)"
[[ "$(docker inspect --format '{{.Image}}' "$container_id")" == "$baseline" ]]
curl --fail --silent http://127.0.0.1:8080/health/ready |
    python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ready"'
printf 'Rollback restored the baseline image and readiness passed.\n'
python3 infra/scripts/smoke-drift.py http://127.0.0.1:8080 verify --snapshot-file "$DRIFT_GUARD_STATE_DIR/drift.json"
docker_compose stop postgres
python3 infra/scripts/smoke-drift.py http://127.0.0.1:8080 outage --snapshot-file "$DRIFT_GUARD_STATE_DIR/drift.json"
docker_compose up --detach --wait --wait-timeout 90 postgres
python3 infra/scripts/smoke-drift.py http://127.0.0.1:8080 verify --snapshot-file "$DRIFT_GUARD_STATE_DIR/drift.json"
python3 infra/scripts/smoke-validation.py http://127.0.0.1:8080
