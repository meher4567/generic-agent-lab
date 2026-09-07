#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
if [[ $EUID == 0 ]]; then
  default_runtime=/home/agentlab/.local/share/generic-agent-lab
else
  default_runtime=${XDG_DATA_HOME:-$HOME/.local/share}/generic-agent-lab
fi
command -v python3 >/dev/null || { echo 'Python 3 is required. Setup logs: /var/log/generic-agent-lab/'; exit 1; }
exec python3 "$repo_dir/agentlab/diagnostics.py" --runtime "${LAB_ROOT:-$default_runtime}" --source "$repo_dir" "$@"
