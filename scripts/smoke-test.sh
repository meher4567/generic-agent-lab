#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
export LAB_SOURCE_ROOT="$repo_dir"
exec "$repo_dir/.venv/bin/labctl" validate "$@"
