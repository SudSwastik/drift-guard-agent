#!/usr/bin/env bash
# Keep the repository root as the Compose project and .env location.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec docker compose --project-directory "$project_dir" --file "$project_dir/infra/compose.yaml" "$@"
