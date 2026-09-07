#!/usr/bin/env bash
# Shared logging for setup, including explicit exits and interrupted commands.

setup_logging() {
  # Set by the sourcing launcher; used only for printed recovery instructions.
  setup_repo_dir=${repo_dir:-.}
  setup_kind=$1
  setup_log_dir=$2
  mkdir -p "$setup_log_dir"
  chmod 0700 "$setup_log_dir"
  setup_log="$setup_log_dir/$setup_kind-$(date -u +%Y%m%dT%H%M%SZ)-$$.log"
  touch "$setup_log"
  chmod 0600 "$setup_log"
  exec > >(tee -a "$setup_log") 2>&1
  phase=preflight
  setup_started=$SECONDS
  setup_line=unknown
  trap 'setup_line=$LINENO' ERR
  trap 'exit 130' INT
  trap 'exit 143' TERM
  trap 'setup_finished "$?"' EXIT
  echo "[INFO] Setup log: $setup_log"
  # Serialize setup before touching shared packages or the installation/venv.
  exec {setup_lock_fd}>"$setup_log_dir/.setup.lock"
  flock -w 10 "$setup_lock_fd" || { echo '[FAIL] Another setup is running. Wait for it to finish, then retry.'; exit 1; }
}

setup_phase() {
  phase=$1
  echo "[RUN ] Setup phase: $phase ($((SECONDS - setup_started))s elapsed)"
}

setup_finished() {
  local code=$1 status=PASS
  trap - EXIT ERR INT TERM
  [[ $code == 0 ]] || status=FAIL
  # Best effort: a full/read-only disk must not hide the original exit code.
  printf '{"status":"%s","phase":"%s","exit_code":%s,"elapsed_seconds":%s,"log_file":"%s"}\n' \
    "$status" "$phase" "$code" "$((SECONDS - setup_started))" "${setup_log##*/}" \
    > "$setup_log_dir/$setup_kind.json" || true
  if [[ $code != 0 ]]; then
    echo "[FAIL] Setup phase: $phase; exit: $code; line: $setup_line"
    echo "Log: $setup_log"
    case "$phase" in
      packages)
        echo 'Check the last apt error for package locks, DNS, disk space, or an interrupted package configuration.'
        echo 'Package locks are allowed 120 seconds; downloads are retried three times. Rerun after the other package manager finishes.' ;;
      python*|application)
        echo 'Check Python/venv availability, disk space, and package-index connectivity. Downloads use bounded retries.' ;;
      account)
        echo 'Check the agentlab account configuration and that this host boots with systemd.' ;;
      network)
        echo 'Inspect: sudo systemctl status libvirtd --no-pager'
        echo 'Inspect: sudo virsh -c qemu:///system net-info default'
        echo 'A conflicting existing subnet or bridge needs resolution before retrying.' ;;
      validation|verification)
        echo 'Setup reached host verification. Inspect: sudo agentlab report --failures'
        echo 'Inspect: sudo agentlab doctor' ;;
    esac
    echo "Collect diagnostics: sudo bash \"$setup_repo_dir/scripts/diagnose.sh\""
    echo "After resolving the cause, rerun the original start command. Log: $setup_log"
  fi
  exit "$code"
}
