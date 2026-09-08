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
| `/etc/systemd/system/user@UID.service.d/70-generic-agent-lab.conf` | Root-owned controller delegation for the dedicated lab user |

The installer enables lingering for the dedicated user, starts its user service, and
uses a fresh `runuser` session so newly granted groups apply immediately. It does not
change the permissions of `/dev/kvm`, disable AppArmor/SELinux, expose a Docker socket,
or create a passwordless sudo rule for the lab account.

The dedicated user manager receives CPU/memory/PID controller delegation, including on
Ubuntu 22.04 systems whose defaults only delegate memory and PIDs. The installer applies
this setting live when possible and verifies actual controller availability before
starting job containers. See [staged recovery](ubuntu-compatibility.md) for fallbacks.

The operator wrapper explicitly selects the lab account's config/data/cache directories
and installed command path. It does not inherit another account's container-storage
configuration when invoked through sudo or an automation runner.

The dedicated account uses its own Podman overlay cache under the runtime. Bootstrap
clears inherited access/default ACLs from the managed source tree and the lab's runtime
and storage directory roots, then applies the documented POSIX modes. This prevents
host-specific named ACL users from becoming invalid IDs inside container namespaces.
It does not change shared parent directories or recurse through writable job data.
The storage driver is recorded in the host report. Existing unmanaged container
configuration is not overwritten, and another account's Podman storage is retained.

## Diagnose one layer at a time

```bash
sudo agentlab doctor --json
sudo agentlab report
sudo agentlab report --failures
sudo agentlab diagnose
sudo systemctl status libvirtd
sudo systemctl status generic-agent-lab-reaper.timer
sudo journalctl -u generic-agent-lab-reaper.service --no-pager -n 30
```

| Failure | Next action |
|---|---|
| Unsupported Ubuntu/architecture | Use Ubuntu 22.04 or 24.04 x86_64 for the full profile |
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
| `RESOURCE_BUSY` | Another operation holds a lab lock; wait for it to finish, then retry |
| `EVENT_LOG_FAILED` | Check `operation_completed` and the returned operation result before retrying; the action may already have succeeded |
| `INVALID_STATE` | Collect diagnostics and preserve the runtime; inspect/restore the corrupted record instead of deleting ownership evidence |
| `REPORT_WRITE_FAILED` | Check free bytes, inodes, and permissions; use the printed emergency report path |
| `VM_STOPPED` | Guest shut down or crashed before readiness; inspect saved state and host/libvirt diagnostics |
| `CGROUP_DELEGATION_MISSING` | Rerun setup to install the dedicated-user controller override; if live activation fails, clean up lab workloads and reboot |
| `SSH_HOST_KEY_MISMATCH` | Inspect VM/IP ownership and saved identity evidence; create a fresh owned VM after cleanup, keeping host-key checks enabled |

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
only the paths in the installation table, the two reaper unit files, and the dedicated
user's controller override. Remove the
dedicated account only if it has no other use. OS packages, the default libvirt network,
and other users' containers/VMs can have unrelated uses and are not automatically removed.

For local development, `LAB_ROOT`, `LAB_VM_STORAGE`, and `LAB_SOURCE_ROOT` configure
operator-owned locations. They are deployment configuration, not caller-controlled
capability arguments. The system-wide QEMU process must be able to traverse the storage
directory; keep private SSH material in the private runtime, not a shared VM image folder.
