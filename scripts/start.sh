#!/usr/bin/env bash
set -Eeuo pipefail
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
case "${1:-}" in
  --help|-h)
    cat <<'HELP'
Usage:
  ./scripts/start.sh                      Install on Ubuntu, then run the full three-VM proof
  ./scripts/start.sh --vm-count 1         Install and run a smaller VM proof (PARTIAL, exit 1)
  ./scripts/start.sh --vm-timeout 600     Allow more time for slow guest startup
  ./scripts/start.sh --check              Validate an already provisioned current account
  ./scripts/start.sh --sandbox            Local Python setup + real rootless sandbox tests
  ./scripts/start.sh --software           Local Python setup + unit and simulated fault tests

Default installation creates a non-root agentlab user, installs apt/Python packages,
enables libvirt/default NAT, configures rootless Podman, and installs a VM expiry timer.
sudo is used for host setup; workloads execute as agentlab. Reruns preserve reports.
HELP
    exit 0 ;;
  --software|--sandbox|--check)
    mode=$1
    shift
    "$repo_dir/scripts/setup-python.sh"
    profile=${mode#--}
    [[ $profile != check ]] || profile=full
    export LAB_SOURCE_ROOT="$repo_dir"
    exec "$repo_dir/.venv/bin/labctl" validate --profile "$profile" "$@" ;;
  ''|--vm-count|--vm-timeout)
    if [[ $EUID == 0 ]]; then
      exec bash "$repo_dir/scripts/bootstrap-ubuntu.sh" --run "$@"
    else
      exec sudo bash "$repo_dir/scripts/bootstrap-ubuntu.sh" --run "$@"
    fi ;;
  *) echo "Unknown option: $1. Use --help." >&2; exit 2 ;;
esac
