#!/usr/bin/env bash
# Run only on an isolated Docker host/project; never against the production project.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
docker_compose() {
    bash "$project_dir/infra/scripts/compose.sh" "$@"
}
export COMPOSE_PROJECT_NAME=drift-guard-rehearsal
export DRIFT_GUARD_ENVIRONMENT=production
export DRIFT_GUARD_STATE_DIR="$(mktemp -d)"
cleanup() {
    docker_compose down --remove-orphans
    rm -rf "$DRIFT_GUARD_STATE_DIR"
}
trap cleanup EXIT

# Different labels guarantee distinct images even when rehearsing the same source.
docker build --file infra/Dockerfile --label rehearsal=baseline --tag drift-guard-agent:baseline .
docker build --file infra/Dockerfile --label rehearsal=candidate --tag drift-guard-agent:candidate .
bash infra/scripts/release.sh deploy drift-guard-agent:baseline
baseline="$(docker image inspect --format '{{.Id}}' drift-guard-agent:baseline)"
bash infra/scripts/release.sh deploy drift-guard-agent:candidate
candidate="$(docker image inspect --format '{{.Id}}' drift-guard-agent:candidate)"
[[ "$baseline" != "$candidate" ]]
bash infra/scripts/release.sh rollback
container_id="$(docker_compose ps --quiet drift-guard)"
[[ "$(docker inspect --format '{{.Image}}' "$container_id")" == "$baseline" ]]
curl --fail --silent http://127.0.0.1:8080/health/ready |
    python3 -c 'import json,sys; assert json.load(sys.stdin)["status"] == "ready"'
printf 'Rollback restored the baseline image and readiness passed.\n'
