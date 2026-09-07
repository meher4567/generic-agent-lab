#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID == 0 ]]; then
  echo 'Run local Python setup as a non-root user; use bootstrap-ubuntu.sh for host installation.' >&2
  exit 1
fi
python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install --require-hashes -r "$repo_dir/requirements.lock"
"$repo_dir/.venv/bin/python" -m pip install --no-deps --no-build-isolation -e "$repo_dir"
