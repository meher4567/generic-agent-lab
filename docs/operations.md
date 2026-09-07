# Operations and troubleshooting

The default installation is deliberately separate from the Git checkout. Updating the
checkout does not change installed broker code until `scripts/start.sh` or the bootstrap
is run again. Installation preserves the account, runtime, base cache, and earlier reports.

| Location | Owner and purpose |
|---|---|
| `/opt/generic-agent-lab/source` | Root-owned reviewed source and tests |
| `/opt/generic-agent-lab/venv` | Root-owned Python installation |
| `/usr/local/bin/agentlab` | Operator wrapper; executes the CLI as the lab account |
| `/home/agentlab/.local/share/generic-agent-lab` | Private job state, reports, artifacts, client keys |
| `/var/lib/libvirt/images/generic-agent-lab` | Lab-owned base and disposable overlay/seed disks |
| `/var/log/generic-agent-lab` | Bootstrap logs and status |
| `generic-agent-lab-reaper.timer` | Checks expired VM records once per minute |

The installer enables lingering for the dedicated user, starts its user service, and
uses a fresh `runuser` session so newly granted groups apply immediately. It does not
change the permissions of `/dev/kvm`, disable AppArmor/SELinux, expose a Docker socket,
or create a passwordless sudo rule for the lab account.

## Diagnose one layer at a time

```bash
sudo agentlab doctor --json
sudo agentlab report
sudo systemctl status libvirtd
sudo systemctl status generic-agent-lab-reaper.timer
sudo journalctl -u generic-agent-lab-reaper.service --no-pager -n 30
```

| Failure | Next action |
|---|---|
| Unsupported Ubuntu/architecture | Use Ubuntu 24.04 x86_64 for the full profile |
| Cannot open/create a KVM descriptor | Enable CPU virtualization or nested virtualization; inspect lab user's kvm group |
| libvirt unavailable | Start libvirtd and check group/session access to `qemu:///system` |
| Default NAT unavailable | Inspect `virsh -c qemu:///system net-info default`; resolve bridge/subnet conflicts |
| Rootless Podman failure | Check subuid/subgid mappings, the user service, cgroups v2, and Podman diagnostics |
| Container limits rejected | Check delegated CPU/memory/PID controllers; the lab will not drop limits silently |
| Insufficient memory/disk | Allocate/free resources; rerun doctor before validation |
| Signed cloud image mismatch | Retry the download; inspect the Ubuntu keyring and image server access |
| `NO_IP` | Inspect the recorded domain/MAC and the default network's DHCP leases |
| `SSH_TIMEOUT` | Check cloud-init boot progress and guest/host NAT connectivity |
| `BOOT_TIMEOUT` | SSH responded but the cloud-init completion marker did not appear |
| `BUILD_FAILED` | Read the job's build log; the initial toy source intentionally fails until patched |
| `STALE_BUILD` / `WRONG_IMAGE_BINDING` | Rebuild the current source, register its image, and create a fresh VM |
| `VM_QUOTA` | Clean up existing lab VM reservations, including failed deployments |
| Cleanup cannot contact libvirt | Restore libvirt, then rerun cleanup; disk state is retained for safe retry |

Each job's `logs/`, `builds/`, `tests/`, `vm/*/domain.xml`, and `events.jsonl` provide
the relevant IDs and local evidence. Runtime evidence can contain local paths, guest
addresses, or diagnostic details. Review it before sharing; it is never part of a normal commit.

## Interruptions and removal

Ctrl-C or SIGTERM during validation triggers cleanup and a failed report. SIGKILL, a
power loss, or an unavailable hypervisor cannot execute an immediate cleanup handler.
The expiry timer and `sudo agentlab cleanup` provide recovery. Cleanup retains enough
state to retry and refuses resources with mismatched ownership.

Before removing the installation, run `sudo agentlab cleanup` successfully and preserve
any reports you need. Stop and disable the `generic-agent-lab-reaper.timer`, then remove
only the paths in the installation table and its two systemd unit files. Remove the
dedicated account only if it has no other use. OS packages, the default libvirt network,
and other users' containers/VMs can have unrelated uses and are not automatically removed.

For local development, `LAB_ROOT`, `LAB_VM_STORAGE`, and `LAB_SOURCE_ROOT` configure
operator-owned locations. They are deployment configuration, not caller-controlled
capability arguments. The system-wide QEMU process must be able to traverse the storage
directory; keep private SSH material in the private runtime, not a shared VM image folder.
