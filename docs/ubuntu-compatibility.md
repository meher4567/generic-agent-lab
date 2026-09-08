# Ubuntu compatibility and staged recovery

The supported host releases are **Ubuntu 22.04 LTS and 24.04 LTS, x86_64**.
Confirm the target with `cat /etc/os-release`: `VERSION_ID` must be `22.04` or `24.04`.
Point releases such as 22.04.5 use `VERSION_ID="22.04"`. A different release, including
22.10, is outside this installer's supported profile and produces a clear stop.

Both supported releases run the actual installer, sandbox workflow, full three-VM
acceptance when the runner meets prerequisites, diagnostic collection, and repeat
installation in CI. See the [validation record](validation-status.md) for passing runs
and the exact tested revision. The host's Ubuntu version is independent of the disposable
Ubuntu 24.04 guest and sandbox fixture used for this infrastructure proof.

## Recovery stages

| Layer | Normal path | Next stage | When work stops |
|---|---|---|---|
| Packages | Official Ubuntu apt packages and hash-locked Python dependencies | Bounded download retries; apt installation-lock wait up to 120 seconds | Persistent repository, DNS, TLS, disk, or package configuration failure; setup log identifies the phase |
| Python | Ubuntu's Python 3.10 on 22.04 or Python 3.12 on 24.04 | Repeat venv/dependency setup after a failed download | Missing/broken interpreter or unrepaired package installation |
| Resource controllers | Dedicated `agentlab` user-service override delegates CPU, memory, PID, I/O and CPU-set controllers | Apply the setting to the live user manager; persistent override also loads on boot | If controllers remain unavailable, host checks fail before job containers start; clean up and reboot if needed |
| Containers | Rootless Podman from the host's Ubuntu packages | Common cleanup flags support both Podman 3.x and 4.x; owned-resource cleanup is repeatable | Ownership mismatch, unavailable runtime, or failed resource/isolation assertions |
| VM hardware profile | Select `ubuntu24.04` from osinfo-db | Select `ubuntu22.04` when that is the available compatible profile; record `os_variant_fallback` | Neither known profile exists; update osinfo-db before retrying |
| Guest readiness | First check uses up to half the configured readiness budget | One additional attempt for `NO_IP`, `SSH_TIMEOUT`, or `BOOT_TIMEOUT`, using the remaining budget | Final deadline, stopped guest, ownership error, or SSH host-key mismatch |
| Evidence | JSON/HTML reports and per-step failure logs | Emergency JSON under `/tmp` if normal report storage fails | If emergency storage also fails, preserve the terminal output |
| Diagnostics | `sudo agentlab diagnose` | `sudo bash scripts/diagnose.sh` from the clone, including when the Python environment is unavailable | Unreadable evidence is listed as omitted; use `--output-dir` on a writable filesystem if necessary |

Transient readiness retries check the same VM; they do not deploy extra guests or
repeat builds. Reports retain `readiness_attempts` and `recovered_after_retry`. A first
attempt timing out can recover to PASS only after the same real guest becomes ready
and its remaining acceptance tests pass. Use `./scripts/start.sh --vm-timeout 600` to
allow a total readiness budget of up to ten minutes per guest.

## Storage and host limitations

Native rootless overlay storage passed on both tested Ubuntu releases. The installer
preserves the dedicated account's managed cache on reruns. An existing storage backend
is not automatically reset or switched: doing that could hide resources belonging to
earlier jobs. Storage failures are reported for inspection and correction before retry.

Hardware virtualization, sufficient RAM/disk, a working cgroups v2 kernel, and network
access remain host prerequisites. Missing hardware or failed identity checks do not
fall back to simulated VMs, privileged containers, disabled resource limits, or disabled
SSH/TLS verification. A full PASS requires the complete real acceptance profile on the
machine where the command runs.

The dedicated controller override is
`/etc/systemd/system/user@UID.service.d/70-generic-agent-lab.conf`, where UID is the
result of `id -u agentlab`. Remove that specific override when removing the lab account;
other users' service configuration is retained.

## Compatibility references

Ubuntu 22.04 packages Podman 3.4.4; its command options differ from newer releases.
The implementation uses the documented common removal flags.
[Ubuntu package](https://packages.ubuntu.com/en/jammy/podman),
[Podman 3.x removal options](https://docs.podman.io/en/v3.4.3/markdown/podman-rm.1.html).

Rootless resource limits depend on cgroups v2 delegation, which systemd controls at
the service boundary. The lab uses an override for its dedicated user manager.
[Systemd delegation documentation](https://github.com/systemd/systemd/blob/main/docs/CGROUP_DELEGATION.md).

Podman documents storage restrictions and the distinction between native and FUSE
overlay. These are diagnostic considerations rather than reasons to discard an
existing cache automatically.
[Podman rootless documentation](https://docs.podman.io/en/v3.4.4/markdown/podman.1.html).
