#!/usr/bin/env bash
set -Eeuo pipefail
umask 022
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
run_validation=false
install_only=false
vm_count=3
while (($#)); do
  case "$1" in
    --run) run_validation=true; shift ;;
    --install-only) install_only=true; shift ;;
    --vm-count)
      [[ ${2:-} =~ ^[123]$ ]] || { echo '--vm-count must be 1, 2, or 3' >&2; exit 2; }
      vm_count=$2; shift 2 ;;
    --help|-h) echo 'Usage: sudo ./scripts/bootstrap-ubuntu.sh [--run | --install-only] [--vm-count 1|2|3]'; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
if $run_validation && $install_only; then
  echo '--run and --install-only are mutually exclusive.' >&2
  exit 2
fi
[[ $EUID == 0 ]] || { echo 'Host installation requires sudo.' >&2; exit 1; }
# Reject unsupported hosts before making any host configuration changes.
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == ubuntu && ($VERSION_ID == 24.04 || $VERSION_ID == 22.04) ]] || {
  echo "[FAIL] Ubuntu 22.04/24.04 required; detected ${PRETTY_NAME}." >&2
  echo 'Use --sandbox or --software for a scoped local check on another Linux host.' >&2
  exit 1
}
[[ $(uname -m) == x86_64 ]] || { echo '[FAIL] This guest profile requires x86_64.' >&2; exit 1; }

install_dir=/opt/generic-agent-lab
lab_user=agentlab
log_dir=/var/log/generic-agent-lab
install -d -m 0755 "$log_dir"
setup_log="$log_dir/bootstrap-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(tee -a "$setup_log") 2>&1
phase=preflight
bootstrap_failed() {
  local code=$?
  printf '[FAIL] Bootstrap phase %s, line %s, exit %s. Log: %s\n' "$phase" "$1" "$code" "$setup_log"
  printf '{"status":"FAIL","phase":"%s","exit_code":%s}\n' "$phase" "$code" > "$log_dir/bootstrap.json"
  exit "$code"
}
trap 'bootstrap_failed "$LINENO"' ERR
if [[ -e $install_dir && ! -f $install_dir/.managed ]]; then
  echo "[FAIL] $install_dir exists without the lab ownership marker; refusing to overwrite it."
  exit 1
fi
for target in /usr/local/bin/agentlab /etc/systemd/system/generic-agent-lab-reaper.service /etc/systemd/system/generic-agent-lab-reaper.timer; do
  if [[ -e $target ]] && ! grep -q 'Managed by generic-agent-lab' "$target"; then
    echo "[FAIL] Unmanaged file exists: $target"
    exit 1
  fi
done

phase=packages
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y software-properties-common
add-apt-repository -y universe
apt-get update
apt-get install -y git python3 python3-venv python3-pip jq curl openssh-client rsync \
  qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients virtinst cloud-image-utils \
  podman uidmap fuse-overlayfs slirp4netns dbus-user-session libosinfo-bin osinfo-db \
  ubuntu-cloudimage-keyring gpgv cpu-checker make shellcheck

phase=account
if ! id "$lab_user" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$lab_user"
fi
if id -nG "$lab_user" | tr ' ' '\n' | grep -Eq '^(sudo|wheel|docker)$'; then
  echo '[FAIL] Existing agentlab account has administrative groups; use a dedicated account without those groups.'
  exit 1
fi
usermod -a -G libvirt,kvm "$lab_user"
lab_uid=$(id -u "$lab_user")
lab_group=$(id -gn "$lab_user")
lab_home=$(getent passwd "$lab_user" | cut -d: -f6)
[[ $lab_uid != 0 && $lab_home == /home/agentlab ]] || { echo '[FAIL] Unexpected agentlab account configuration.'; exit 1; }
python3 "$repo_dir/scripts/configure-subids.py" "$lab_user"
loginctl enable-linger "$lab_user"
systemctl start "user@${lab_uid}.service"
systemctl enable --now libvirtd.service

phase=network
if ! virsh --connect qemu:///system net-list --all --name | grep -qx default; then
  network_template=/usr/share/libvirt/networks/default.xml
  [[ -f $network_template ]] || { echo '[FAIL] libvirt default network template missing.'; exit 1; }
  virsh --connect qemu:///system net-define "$network_template"
fi
if ! virsh --connect qemu:///system net-list --name | grep -qx default; then
  virsh --connect qemu:///system net-start default
fi
virsh --connect qemu:///system net-autostart default

phase=application
install -d -m 0755 "$install_dir" "$install_dir/source"
touch "$install_dir/.managed"
rsync -a --delete \
  --exclude='__pycache__' --exclude='*.pyc' \
  --include='/agentlab/***' --include='/containers/***' --include='/scripts/***' \
  --include='/tests/***' --include='/docs/***' --include='/pyproject.toml' \
  --include='/requirements.lock' --include='/README.md' --include='/LICENSE' \
  --exclude='*' "$repo_dir/" "$install_dir/source/"
chown -R root:root "$install_dir/source"
chmod -R a+rX,go-w "$install_dir/source"
python3 -m venv "$install_dir/venv"
"$install_dir/venv/bin/python" -m pip install --require-hashes -r "$install_dir/source/requirements.lock"
"$install_dir/venv/bin/python" -m pip install --no-deps --no-build-isolation "$install_dir/source"
runtime="$lab_home/.local/share/generic-agent-lab"
install -d -m 0700 -o "$lab_user" -g "$lab_group" "$lab_home/.local" "$lab_home/.local/share" "$runtime"
vm_storage=/var/lib/libvirt/images/generic-agent-lab
if [[ -e $vm_storage && $(stat -c %U "$vm_storage") != "$lab_user" ]]; then
  echo '[FAIL] VM storage exists with another owner; refusing to change its ownership.'
  exit 1
fi
install -d -m 0711 -o "$lab_user" -g "$lab_group" "$vm_storage"

# This wrapper is an operator entry point. It grants no sudo rights to agentlab.
cat > /usr/local/bin/agentlab <<'WRAPPER'
#!/usr/bin/env bash
# Managed by generic-agent-lab
set -Eeuo pipefail
if [[ $EUID != 0 ]]; then
  echo 'Use sudo agentlab <command>; the command itself runs as the non-root lab account.' >&2
  exit 1
fi
lab_uid=$(id -u agentlab)
cd /opt/generic-agent-lab/source
exec runuser -u agentlab -- env \
  XDG_RUNTIME_DIR="/run/user/$lab_uid" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$lab_uid/bus" \
  LAB_ROOT=/home/agentlab/.local/share/generic-agent-lab \
  LAB_VM_STORAGE=/var/lib/libvirt/images/generic-agent-lab \
  LAB_SOURCE_ROOT=/opt/generic-agent-lab/source \
  /opt/generic-agent-lab/venv/bin/labctl "$@"
WRAPPER
chmod 0755 /usr/local/bin/agentlab

phase=expiry_timer
cat > /etc/systemd/system/generic-agent-lab-reaper.service <<SERVICE
# Managed by generic-agent-lab
[Unit]
Description=Remove expired generic lab VMs
After=libvirtd.service
[Service]
Type=oneshot
User=$lab_user
Group=$lab_group
SupplementaryGroups=libvirt kvm
Environment=LAB_ROOT=$runtime
Environment=LAB_VM_STORAGE=$vm_storage
Environment=XDG_RUNTIME_DIR=/run/user/$lab_uid
WorkingDirectory=$install_dir/source
ExecStart=$install_dir/venv/bin/labctl vm reap
UMask=0077
SERVICE
cat > /etc/systemd/system/generic-agent-lab-reaper.timer <<'TIMER'
# Managed by generic-agent-lab
[Unit]
Description=Check generic lab VM lifetimes every minute
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
[Install]
WantedBy=timers.target
TIMER
systemctl daemon-reload
systemctl enable --now generic-agent-lab-reaper.timer
phase=verification
printf '{"status":"PASS","phase":"installed"}\n' > "$log_dir/bootstrap.json"
echo '[PASS] Ubuntu packages, dedicated account, libvirt, NAT, rootless runtime, CLI, and expiry timer installed.'
echo "Bootstrap log: $setup_log"
if $install_only; then
  echo 'Installation complete. Run sudo agentlab validate to test the environment.'
elif $run_validation; then
  phase=validation
  agentlab validate --profile full --vm-count "$vm_count"
else
  agentlab doctor
  echo 'Run: sudo agentlab validate'
fi
