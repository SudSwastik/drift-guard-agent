#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$project_dir"
export UV_CACHE_DIR="${UV_CACHE_DIR:-$project_dir/.uv-cache}"
export UV_TOOL_DIR="${UV_TOOL_DIR:-$UV_CACHE_DIR/tools}"
check_dir="$(mktemp -d)"
trap 'rm -rf "$check_dir"' EXIT

uv sync --locked --all-groups
uv run --locked ruff format --check drift_guard tests infra/scripts/smoke-validation.py
uv run --locked ruff check .
uv run --locked mypy drift_guard
uv run --locked pytest
bash -n infra/scripts/compose.sh infra/scripts/release.sh infra/scripts/check-rollback.sh

uv export --locked --no-dev --no-emit-project --output-file "$check_dir/runtime.lock" --quiet
diff <(tail -n +3 infra/requirements.lock) <(tail -n +3 "$check_dir/runtime.lock")

uv export --locked --all-groups --no-emit-project --output-file "$check_dir/audit.lock" --quiet
uv tool run pip-audit==2.10.1 --requirement "$check_dir/audit.lock" \
    --no-deps --disable-pip --strict --cache-dir "$UV_CACHE_DIR/pip-audit"
