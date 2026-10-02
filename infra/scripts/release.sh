#!/usr/bin/env bash
# Deploy a prebuilt image, preserving the actual previous image for rollback.
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
docker_compose() {
    bash "$project_dir/infra/scripts/compose.sh" "$@"
}
state_dir="${DRIFT_GUARD_STATE_DIR:-$project_dir/.deploy}"
mkdir -p "$state_dir"
if ! mkdir "$state_dir/lock" 2>/dev/null; then
    printf 'Another release is running; state lock: %s\n' "$state_dir/lock" >&2
    exit 1
fi
trap 'rmdir "$state_dir/lock"' EXIT

case "${1:-}" in
    deploy)
        if [[ $# != 2 ]]; then
            printf 'Usage: bash infra/scripts/release.sh deploy IMAGE\n' >&2
            exit 1
        fi
        target="$2"
        ;;
    rollback)
        if [[ ! -s "$state_dir/previous-image" ]]; then
            printf 'No previous release recorded. Deploy once while the old container is running.\n' >&2
            exit 1
        fi
        target="$(cat "$state_dir/previous-image")"
        ;;
    *)
        printf 'Usage: bash infra/scripts/release.sh deploy IMAGE | rollback\n' >&2
        exit 1
        ;;
esac

# Resolve tags to immutable image IDs before touching the running service.
target_id="$(docker image inspect --format '{{.Id}}' "$target")"
container_id="$(docker_compose ps --all --quiet drift-guard)"
previous_id=""
if [[ -n "$container_id" ]]; then
    previous_id="$(docker inspect --format '{{.Image}}' "$container_id")"
fi

start_image() {
    docker_compose up --detach --wait --wait-timeout 90 postgres || return 1
    DRIFT_GUARD_IMAGE="$1" docker_compose up --detach --no-build --pull never \
        --no-deps --force-recreate --wait --wait-timeout 90 drift-guard
}

if ! start_image "$target_id"; then
    printf 'Release failed readiness checks.\n' >&2
    docker_compose logs --tail=50 drift-guard >&2 || true
    if [[ -n "$previous_id" ]]; then
        printf 'Restoring previous image: %s\n' "$previous_id" >&2
        start_image "$previous_id" || {
            printf 'Recovery failed; inspect the Compose service logs immediately.\n' >&2
            exit 1
        }
    fi
    exit 1
fi

if [[ -n "$previous_id" && "$previous_id" != "$target_id" ]]; then
    printf '%s\n' "$previous_id" > "$state_dir/previous-image"
fi
printf '%s\n' "$target_id" > "$state_dir/current-image"
printf 'Healthy release: %s\n' "$target_id"
printf 'Use release.sh for subsequent releases; plain compose up may select its default image.\n'
