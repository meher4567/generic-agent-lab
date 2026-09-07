#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID == 0 ]]; then
  echo 'Run local Python setup as a non-root user; use bootstrap-ubuntu.sh for host installation.' >&2
  exit 1
fi
# shellcheck source=scripts/setup-common.sh
source "$repo_dir/scripts/setup-common.sh"
setup_logging python-setup "$repo_dir/reports/setup"
setup_phase python_venv
command -v python3 >/dev/null || { echo '[FAIL] Install Python 3.10+ and its venv module first.'; exit 1; }
python3 -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10 or newer is required"'
python3 -m venv "$repo_dir/.venv"
setup_phase python_dependencies
"$repo_dir/.venv/bin/python" -m pip install --disable-pip-version-check --retries 3 --timeout 30 --require-hashes -r "$repo_dir/requirements.lock"
"$repo_dir/.venv/bin/python" -m pip install --disable-pip-version-check --no-deps --no-build-isolation -e "$repo_dir"
