# Implementation, issues encountered, and validation report

**Record date:** 2026-09-08

**Repository:** [generic-agent-lab](https://github.com/meher4567/generic-agent-lab)

**Latest implementation tested:** `ddef475c741d58f97ccf5399d0e861b6658e5210`

The lab was implemented, published, debugged on the Fedora development machine, and
then tested through actual installation on fresh Ubuntu hosts. The latest full runs
on **Ubuntu 22.04 LTS and Ubuntu 24.04 LTS each passed 101 checks with three real KVM
guests**, with no failed or skipped checks and `environment_ready: true`.

This document explains what was built, which problems actually occurred, how they
were fixed, what recovery mechanisms now exist, and what still depends on the target
machine. It is a record of completed work and observed evidence, not a guarantee that
every future host will start without an error. The commands below let a new host
produce its own readiness result.

## 1. What the project was intended to prove

The supplied infrastructure guide was used to build a standalone proof that an
engineering-agent environment can run its infrastructure workflow before a model or
real project is connected. A small Python fixture supplies the source, deliberate bug,
patch, build artifact, and guest smoke test.

The implemented workflow answers these questions:

1. Can installation provision the host and a dedicated non-root controller account?
2. Can each job use an isolated container while its protected reference stays read-only?
3. Can structured tools read files, inspect Git, apply an approved patch, and build code?
4. Can the broker reject another job's files, stale artifacts, and mismatched VM identities?
5. Can the host boot three disposable VMs, discover their addresses, and test them over SSH?
6. Can expected failures produce useful evidence and leave resources available for cleanup?
7. Can a fresh checkout on another Ubuntu host repeat the installation and full test?

The repository contains generic fixtures and infrastructure code. It has no model API
requirement, production source, or company-specific workload. The original planning
document remains local and is excluded from publication.

## 2. What was built

### Installation and one-command validation

[`scripts/start.sh`](../scripts/start.sh) is the main entry point. On a supported Ubuntu
host its default path performs privileged setup and then runs the full validation.

The installer provisions Ubuntu packages, hash-locked Python dependencies, the
`agentlab` account, subordinate user/group IDs for rootless containers, libvirt/KVM
access, the default NAT/DHCP network, a persistent user manager, and a VM expiry timer.
It installs reviewed source and the Python environment under `/opt/generic-agent-lab`.

The operator uses `sudo agentlab ...`, but the wrapper switches to the dedicated lab
account before executing the controller. The lab account receives no sudo permission.
Its libvirt access is powerful and is intended for a trusted local broker.

Installation can be repeated. It refreshes managed application files while retaining
the account, cached base image, runtime ownership records, and previous reports.
Existing unmanaged files at reserved installation locations produce a clear error.

### Job state and structured tools

The Python controller provides a finite capability registry with validated inputs and
structured results. There is no caller-supplied host shell command, arbitrary VM XML,
arbitrary SSH destination, or arbitrary image-download URL.

Jobs and their source, patch, build, artifact, image, VM, and test records receive IDs.
Mutations use file locks. State is written atomically, and events are appended and
flushed to disk. This makes a failed operation traceable to its job and resource.

File tools confine reads to the registered workspace and reject traversal, symlink
components, hardlinks, special files, and oversized input. Git, patch, and build
execution takes place inside the job sandbox.

### Real rootless container isolation

Each sandbox mounts its writable workspace at `/job` and a read-only generic fixture
at `/reference`. Broker state, keys, artifacts in trusted storage, and other jobs are
outside those mounts.

The container has a read-only root filesystem, no external network, dropped Linux
capabilities, and `no-new-privileges`. Its configured limits are 512 MiB RAM, one CPU
of quota, 128 PIDs, and a 256 MiB temporary filesystem. Validation inspects the active
kernel limits and exercises the mounts and network behavior.

Subprocesses use argument arrays, bounded output, and deadlines. A real execution
timeout also stops the container so the timed-out workload cannot continue running.

### Patch, build, and artifact binding

The toy project initially fails its test. The workflow verifies that failure, checks
and applies the approved fix, then performs compilation, tests, and a real wheel build.
An invalid patch and later source changes exercise rejection and stale-build handling.

The broker hashes accepted source files, including untracked files, before and after
the build. It imports a bounded wheel into trusted storage and records its digest.
Changing the selected patch or current source invalidates the previous build evidence.

An `IMAGE-ID` identifies a deployment manifest binding a job, successful build, wheel
digest, and base-image digest. It is not a separately baked operating-system image.
The wheel is transferred to the guest over SSH and verified there.

### Real VMs and SSH tests

The broker downloads an official Ubuntu cloud image, verifies its signed checksum
list with Ubuntu's packaged keyring, verifies its SHA-256 digest, and caches it by
digest. Each VM receives a separate writable overlay and cloud-init seed.

The fixed VM profile uses one vCPU, 1536 MiB RAM, and a 12 GiB virtual disk. At most
three outstanding VMs are allowed. VM ownership is checked using recorded UUIDs and
XML metadata, not just a matching name.

IP discovery matches the VM's MAC address to libvirt DHCP data and validates the
address against the configured network. Each VM receives separate client and host
SSH keys. Its expected host key is pinned before the first connection.

Guest tests verify the deployment marker, artifact digest, Python execution, writable
disk, and toy function. Cleanup verifies domain removal and deletes that VM's overlay,
seed, and private keys while retaining the base image and reports.

### Reports, diagnostics, and CI

Each validation run writes JSON, HTML, Markdown, software-test logs, and JUnit evidence.
Reports include check statuses, IDs, the first failure, recovery guidance, and explicit
simulation markers for controlled faults. Long operations emit progress heartbeats.

The diagnostic collector creates a private local archive. A standalone script can
collect evidence even if application dependencies were not successfully installed.
Nothing is uploaded automatically.

GitHub Actions runs software tests on Python 3.10, 3.12, and 3.13, plus actual Ubuntu
22.04 and 24.04 installation jobs. Those jobs exercise real containers, attempt the
full three-VM profile when host prerequisites pass, repeat installation, collect
diagnostics, and clean up. The recorded passing runs below did execute the full VM
profile; they were not merely green software jobs.

## 3. How the work progressed

| Stage | Work completed | What it established |
|---|---|---|
| Initial implementation | Controller, fixture, file/Git/patch tools, sandbox, build, VM broker, reports, installer, and CI | A complete executable workflow based on the guide |
| Fedora development checks | Software tests, real rootless containers, and a supplementary real Ubuntu guest | Core behavior worked; the development machine did not meet full Ubuntu acceptance requirements |
| Fresh Ubuntu 24.04 debugging | Reinstallation, environment inheritance, command parsing, and filesystem ACL problems were diagnosed and fixed | Actual Ubuntu provisioning and full validation worked |
| Two-host reproduction | Same implementation installed on two separate fresh Ubuntu 24.04 hosted workers | Full workflow could be reproduced outside the development machine |
| Error-handling expansion | Setup traps, bounded locks, failure evidence, report fallback, robust cleanup, and standalone diagnostics | Common failure paths became easier to diagnose and recover |
| Ubuntu 22.04 expansion | Added a real OS matrix; fixed CPU delegation and older Podman cleanup compatibility | The target LTS release was tested directly |
| Final compatibility verification | Added staged readiness handling and recorded hardware-profile selection | Both supported Ubuntu releases passed the current full implementation |

## 4. Problems actually encountered and how they were handled

These were observed during development or actual host/CI runs. Deliberately injected
failures and additional regression scenarios are described separately in section 5.

### 4.1 The Fedora machine could not satisfy full acceptance

**Observed:** The development host was Fedora, had approximately 7.1 GiB total RAM,
and roughly 9 GiB free disk at the time of testing. Its full run failed the supported-OS
and resource checks. It also lacked the complete unattended libvirt/network/timer
configuration expected by the Ubuntu installation.

**Reason:** Working development tools and visible KVM support do not establish that a
machine meets the full three-VM acceptance profile. The profile requires the supported
Ubuntu host and substantially more memory and disk.

**Action:** The full requirements were retained. Software and sandbox modes were used
for local development, and full acceptance was moved to suitably sized fresh Ubuntu
hosts. Missing local development utilities were installed to permit additional checks.

**Result:** Real local containers and a supplementary single Ubuntu guest passed.
That smaller VM result remained `PARTIAL`. The Fedora machine was never reported as
ready for the complete Ubuntu profile.

### 4.2 Local libvirt access required interactive authorization

**Observed:** The Fedora controller could not perform unattended system-libvirt
operations under its existing authorization configuration.

**Reason:** The development account did not have the same service access and fresh
session that the Ubuntu installer provisions for the dedicated account.

**Action:** A narrowly scoped temporary local polkit rule enabled the development VM
check. It was removed after testing. The Ubuntu installer instead configures the
dedicated account's libvirt/KVM groups and executes through a fresh `runuser` session.

**Result:** The local real-guest check completed, and the actual Ubuntu installation
subsequently passed without requiring that temporary Fedora rule.

### 4.3 Reinstallation left stale generated application files

**Observed:** Earlier installation synchronization could retain generated source or
build files from an earlier install.

**Reason:** Copying the current source alone did not remove obsolete files excluded
from the intended installed source set.

**Fix:** The installer now uses an explicit rsync allowlist with `--delete` and
`--delete-excluded` for its managed source directory.

**Verification:** CI places a stale sentinel in the installation, runs installation
again, and verifies its removal. Runtime data and cached VM images are outside that
source synchronization.

### 4.4 The invoking user's configuration leaked into the lab session

**Observed:** A sudo/automation environment could pass another account's XDG config
and data paths into the lab process, causing rootless Podman to use inappropriate or
inaccessible locations.

**Reason:** Switching the Unix user did not, by itself, guarantee all inherited
environment paths pointed to the dedicated account.

**Fix:** The installed wrapper explicitly sets the lab account's config, data,
cache, runtime, D-Bus, and executable paths. Managed container storage also has an
account-specific configuration.

**Verification:** CI deliberately supplies unsuitable operator XDG paths and checks
that the actual sandbox workflow still succeeds through the installed wrapper.

### 4.5 Command warnings broke structured-output parsing

**Observed:** A host command could emit valid JSON or XML on standard output and
warnings on standard error. Combining those streams made the result fail parsing.

**Reason:** Human-readable diagnostics and machine-readable output were being handled
as one stream for commands that require strict parsing.

**Fix:** The subprocess layer supports separate stderr capture. Callers that expect
JSON/XML parse stdout while keeping stderr available as diagnostic evidence.

**Verification:** Regression coverage and subsequent real host/container/VM runs
confirmed that warnings no longer corrupt those structured responses.

### 4.6 Early failures did not expose enough debugging information

**Observed:** Some initial host and validation failures reported a generic command
failure without enough output to identify its cause quickly.

**Fix:** Error results and per-step logs now retain relevant command output, exit code,
deadline information, and error classification. Host checks attach actionable
diagnostics, and reports surface the first failure and suggested next actions.

**Verification:** Later failing CI runs exposed concrete causes, including the
Ubuntu 22.04 controller and cleanup errors described below. The installed and
standalone diagnostic commands were also exercised in passing CI runs.

### 4.7 Inherited filesystem ACLs broke rootless image operations

**Observed:** On fresh Ubuntu CI hosts, operations inside the rootless image failed
while handling permissions or preserving file metadata. This was a significant
installation blocker even though the ordinary file modes did not explain it.

**Investigation:** Container storage settings, ACL listings, and syscall evidence were
collected. The container-visible ACLs included an unmapped named user displayed as
`4294967295`. A metadata-preserving operation attempted to write
`system.posix_acl_access` and received `EINVAL` (`Invalid argument`).

**Cause:** Managed files/directories inherited host ACL entries naming users outside
the rootless container's subordinate-ID mapping. Those entries became invalid when
preserved inside that namespace.

**Attempt that did not solve it:** A VFS storage-driver workaround was tried. Changing
the driver did not resolve the inherited ACL cause. The final configuration returned
to overlay storage after correcting the managed directory ACLs.

**Fix:** Bootstrap removes inherited access/default ACLs from the root-managed source
tree and selected managed runtime/storage directory roots, then restores explicit
POSIX permissions. It does not recursively modify writable job data or shared parent
directories.

**Verification:** Full Ubuntu 24.04 validation then passed on two fresh hosted workers.
The final Ubuntu 22.04 and 24.04 runs also passed with overlay storage.

**Operational consequence:** There is no automatic destructive storage reset or
driver-switch fallback. A storage problem retains its evidence and requires diagnosis.

### 4.8 Ubuntu 22.04 did not delegate the CPU controller

**Observed in the first real 22.04 CI run:**

```text
Error: OCI runtime error: the requested cgroup controller `cpu` is not available
```

**Cause:** That rootless Podman 3.4.4 session exposed memory and PID controllers but
not CPU control. Merely checking rootless mode and cgroups v2 was insufficient to
guarantee the sandbox's CPU quota could be enforced.

**Fix:** Bootstrap installs a controller-delegation override scoped to the dedicated
user's `user@UID.service`, requesting `cpu cpuset io memory pids`. It reloads systemd,
starts the user manager, and attempts to apply the same setting live. Host checks now
require actual CPU, memory, and PID controller availability before job containers start.

**Fallback:** The persistent override also applies at boot. If live activation fails
and controllers remain unavailable, the tool reports the failure and directs the
operator to clean up workloads and reboot. It does not silently remove CPU limits.

**Verification:** The final 22.04 report records the available controllers, and the
real sandbox quota assertions and complete three-VM workflow pass.

### 4.9 An older Podman version rejected the cleanup flag

**Observed in the same 22.04 run:**

```text
Error: unknown flag: --time
```

**Cause:** The earlier container-removal command used a flag unavailable in Ubuntu
22.04's Podman 3.x. This also made cleanup fail after the initial container error.

**Fix:** Owned-container removal now uses the common `podman rm --force` form.
Ownership is checked before removal, and absence is checked afterward.

**Verification:** Regression coverage checks the compatible invocation. Actual
container cleanup passed on Podman 3.4.4 and 4.9.3 in the final OS matrix.

The initial failing run is retained as [Ubuntu 22.04 failure evidence](https://github.com/meher4567/generic-agent-lab/actions/runs/34183205130).
It is superseded by the passing implementation recorded in section 7.

### 4.10 A CLI exception-handling dependency assumption failed during development

**Observed:** A draft error-handler change imported standalone `click`, and test
collection raised `ModuleNotFoundError: No module named 'click'`.

**Cause:** The locked Typer version supplied its own Click implementation rather than
installing a standalone `click` package. The draft code assumed an undeclared dependency.

**Fix:** Exception handling was changed to use Typer's exposed interfaces and group
implementation. An extra unpinned dependency was not added.

**Verification:** The corrected CLI and error tests pass across all three supported
Python test versions. This was a development failure fixed before publication of
the completed error-handling change.

## 5. Additional failure handling added and tested

The following scenarios were exercised with regression tests or controlled faults.
They should not be read as claims that the development host physically ran out of
disk, lost its network, or suffered every listed incident.

| Scenario | Implemented behavior |
|---|---|
| Explicit setup failure or SIGTERM | Save the phase, exit status, and setup log through the exit/signal handlers |
| Concurrent setup | Reject the competing setup after a bounded lock wait, with a clear explanation |
| Busy broker state | Return `RESOURCE_BUSY` after a bounded 30-second lock wait |
| Corrupt state or a damaged resource record | Return structured errors; preserve ownership evidence and allow independent cleanup where possible |
| Build fails, then recording its failure also fails | Preserve the original build error and attach the secondary persistence problem |
| Operation succeeds, then its event cannot be written | Return `EVENT_LOG_FAILED` with `operation_completed: true` and the operation result so a retry is not made blindly |
| Container execution times out and stopping it also fails | Preserve the timeout as the primary error and report the stop failure too |
| Normal report storage fails | Continue cleanup and attempt an emergency JSON report under `/tmp` |
| Emergency report storage also fails | Preserve the terminal failure information; do not claim a durable report exists |
| Already destroyed VM history with libvirt offline | Skip completed historical VM records during manual cleanup |
| Libvirt is unavailable for a VM still needing cleanup | Retain state for retry; an inaccessible hypervisor is not treated as proof the VM is absent |
| Guest readiness fails | Save available VM state, IP, SSH output, and cloud-init diagnostics when SSH is reachable |
| Guest is stopped or its SSH host key mismatches | Return a specific permanent error promptly |
| Diagnostic evidence is missing or unreadable | List the omission and collect the remaining permitted evidence |
| Diagnostic input is a symlink, hardlink, or special file | Exclude it; use bounded reads and best-effort credential redaction |
| Application dependencies are unavailable | Permit the standalone standard-library diagnostic collector to run |

The completed-VM cleanup issue was a regression found during the error-handling work:
cleanup should not need a working hypervisor just to revisit already destroyed
historical entries. A dedicated regression test now preserves that behavior.

The acceptance workflow also deliberately tests build failure, invalid patches,
forbidden file access, cross-job access, stale builds, incorrect artifact/VM binding,
and rejection of a fourth VM. These checks pass when the expected rejection occurs.

Controlled fault switches cover build timeout, disk-full and dependency errors,
DHCP/SSH/boot timeouts, stale VM IDs, incorrect image binding, SSH disconnection, and
hypervisor unavailability. Their results are marked `simulated: true`. Separate real
checks exercise a failing build, container timeout, guest boot, SSH, and cleanup.

## 6. What the fallback sequence actually does

Recovery is bounded and specific to the failed layer. It does not endlessly restart
the entire environment or turn failed real checks into simulated success.

| Layer | First action | Next action or stopping condition |
|---|---|---|
| Ubuntu packages | Install from the configured Ubuntu repositories | Wait up to 120 seconds for the apt installation lock and use three download retries; persistent failure records the setup phase |
| Python dependencies | Install the hash-locked requirements | Bounded network retries; rerun setup after repairing the underlying package/network/interpreter problem |
| Rootless controllers | Install the dedicated user-service delegation setting | Apply it live; if controllers remain missing, stop before job containers and use the documented reboot recovery |
| VM hardware profile | Select the known `ubuntu24.04` osinfo profile | Use known `ubuntu22.04` profile if that is available instead; record the fallback; fail clearly if neither exists |
| Guest readiness | Use up to half the configured readiness budget | One further attempt for `NO_IP`, `SSH_TIMEOUT`, or `BOOT_TIMEOUT`, using the remaining budget on the same VM |
| Report output | Write normal run evidence | Attempt emergency JSON under `/tmp`; retain terminal output if that also fails |
| Diagnostics | Run the installed `diagnose` command | Use the standalone collector from the clone; choose another output filesystem if necessary |
| Cleanup | Remove resources whose stored ownership matches | Keep unresolved records and rerun after the runtime/hypervisor is available |

The default guest readiness budget is 240 seconds per VM. `--vm-timeout 600` increases
it to at most ten minutes per VM. Both readiness stages share that budget; the second
stage does not grant another full timeout, rebuild the artifact, or deploy another VM.
Reports record `readiness_attempts` and `recovered_after_retry`.

Ownership errors, wrong bindings, host-key mismatches, and stopped VMs are not retried
as temporary readiness failures. Missing KVM, insufficient resources, and failed
isolation checks need their actual cause resolved.

Both final real Ubuntu hosts selected `ubuntu24.04` with no hardware-profile fallback.
The older-catalog fallback and staged timeout recovery were verified through regression
tests; the passing real runs do not prove that every fallback branch occurred in CI.

## 7. Test results and evidence

### Latest implementation

The [final Ubuntu 22.04 / 24.04 workflow](https://github.com/meher4567/generic-agent-lab/actions/runs/34184097989)
tested commit `ddef475c741d58f97ccf5399d0e861b6658e5210`.

| Test scope | Recorded result |
|---|---|
| Ubuntu 22.04 full validation | **101 PASS, 0 FAIL, 0 SKIP, 0 WARN; three real concurrent guests; `environment_ready: true`** |
| Ubuntu 24.04 full validation | **101 PASS, 0 FAIL, 0 SKIP, 0 WARN; three real concurrent guests; `environment_ready: true`** |
| Software/fault suite on Python 3.10 | 82 tests passed |
| Software/fault suite on Python 3.12 | 82 tests passed |
| Software/fault suite on Python 3.13 | 82 tests passed |
| Local real Podman integration suite | 2 tests passed |
| Actual installation and repeat installation | Passed on both Ubuntu releases |
| Installed and standalone diagnostics | Exercised successfully in the Ubuntu workflow |
| Owned-resource cleanup | Passed in both full Ubuntu runs |

The 82 software tests and 101 workflow checks are different measurement layers.
They should not be added together as if they were all distinct end-to-end tests.
The 101 checks include both actual operations and explicitly labeled controlled faults.

| Detail | Ubuntu 22.04 | Ubuntu 24.04 |
|---|---|---|
| Validation run ID | `RUN-273521311d1147b4b51c825cf5e901ab` | `RUN-22c19e2ab32f4373b485920f731a2559` |
| Podman version observed | 3.4.4 | 4.9.3 |
| Storage driver observed | overlay | overlay |
| cgroups mode | v2 | v2 |
| Controllers recorded | cpuset, cpu, io, memory, pids | cpuset, cpu, io, memory, pids |
| Guest/base operating system | Ubuntu 24.04 | Ubuntu 24.04 |

The real VM evidence includes simultaneous guests, DHCP discovery, pinned SSH,
cloud-init readiness, artifact smoke tests, quota rejection, and verified cleanup.
Changing the host release did not change the guest or container fixture release.

### Earlier milestones

| Milestone | Evidence |
|---|---|
| First complete fresh Ubuntu 24.04 run | [Run 34156087327](https://github.com/meher4567/generic-agent-lab/actions/runs/34156087327): 101 passing checks and three real guests |
| Second separate fresh Ubuntu 24.04 worker at the same revision | [Run 34156558776](https://github.com/meher4567/generic-agent-lab/actions/runs/34156558776): independently repeated all 101 checks |
| Expanded error-handling implementation | [Run 34158445245](https://github.com/meher4567/generic-agent-lab/actions/runs/34158445245): 68 software tests per Python version and 101 full Ubuntu checks |
| Ubuntu 22.04 fixes and staged readiness | [Run 34183719814](https://github.com/meher4567/generic-agent-lab/actions/runs/34183719814): both OS versions passed the full profile |

The two initial separate-worker runs used the same implementation,
`35c09ada08df48dc88508c3a22663c1ba0593039`. Their comparison supplies remote reproduction
evidence. An individual report still records
`second_host_reproduction: NOT_VERIFIED_BY_THIS_RUN` because a single run cannot
independently verify the other host.

A fresh clone of the public repository was also used for local setup/software checks.
That verifies public packaging and the local setup path; the separate Ubuntu runs
provide the full host-installation and VM evidence.

Future hosted workers may have different capabilities. A green CI result alone does
not prove VM readiness if the conditional full-profile step was skipped. Always check
the exact revision, run report, profile, VM count, and `environment_ready` field.

## 8. Files and host configuration created

| Location | Purpose |
|---|---|
| `/opt/generic-agent-lab/source` | Root-owned installed application, tests, and documentation |
| `/opt/generic-agent-lab/venv` | Installed Python environment |
| `/usr/local/bin/agentlab` | Operator wrapper that runs the controller as the lab account |
| `/home/agentlab/.local/share/generic-agent-lab` | Private job state, reports, artifacts, and container storage |
| `/home/agentlab/.config/containers/storage.conf` | Dedicated managed rootless storage configuration |
| `/var/lib/libvirt/images/generic-agent-lab` | Cached base images and disposable per-VM disks |
| `/var/log/generic-agent-lab` | Bootstrap logs and status |
| `/etc/systemd/system/user@UID.service.d/70-generic-agent-lab.conf` | Controller delegation for the UID returned by `id -u agentlab` |
| `generic-agent-lab-reaper.service` and `.timer` | Expired-VM cleanup, checked once per minute |

The installer also enables lingering for the dedicated user and configures libvirt's
default NAT network. Shared OS packages and network services can have other users;
the removal procedure does not assume they are disposable.

The development VM/container resources owned by this lab were cleaned up after their
tests, and the temporary Fedora authorization rule was removed. Base-image caches and
local reports were intentionally retained. Public CI runs also verified their owned
VM/container cleanup.

Cleanup is not an uninstall command. It preserves reports, base images, installed
packages, the application, and account configuration. See
[operations and removal](operations.md#interruptions-and-removal) before deleting
installation directories or ownership records.

## 9. How to use this on the target Ubuntu 22.04 machine

### Confirm the target and run

This implementation supports **Ubuntu 22.04 LTS and 24.04 LTS on x86_64**.
"22.XX" should be checked explicitly: the supported release is 22.04, including its
point releases, not every Ubuntu release whose name starts with 22.

```bash
cat /etc/os-release
uname -m
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/meher4567/generic-agent-lab.git
cd generic-agent-lab
./scripts/start.sh
```

The full profile requires at least four logical CPUs, approximately 16 GB total RAM,
approximately 5.5 GiB currently available RAM for three guests, and 80 GiB free on the
runtime and VM-storage filesystems. If both paths share a filesystem, the 80 GiB
requirement is not additive. Hardware or nested KVM virtualization must actually work.
Initial setup also requires working package and image downloads.

For a slower guest startup, use this instead of the final command above:

```bash
./scripts/start.sh --vm-timeout 600
```

### Read the decision

```bash
sudo agentlab report
sudo agentlab report --failures
sudo agentlab doctor
```

The required outcome is `status: PASS`, `profile: full`, three requested VMs, and
`environment_ready: true`. A scoped `PARTIAL` result is useful evidence but does not
establish complete environment readiness. Missing full-profile requirements produce
a non-zero exit status.

### If setup or validation fails

1. Start with the first failed phase/check and its error code. Later failures may be
   consequences of that first problem.
2. Read `sudo agentlab report --failures` when a report exists. Check setup logs under
   `/var/log/generic-agent-lab` if installation itself failed.
3. Collect diagnostics with `sudo agentlab diagnose`.
4. If the installed command is unavailable, run `sudo bash scripts/diagnose.sh` from
   the clone. This collector does not require the application's third-party packages.
5. Resolve the reported cause, run `sudo agentlab doctor`, and repeat validation or
   setup as appropriate.

The installed collector runs as the lab account and may list root-owned setup logs
as unreadable omissions. The standalone command under sudo can collect those logs.
For local `--software` or `--sandbox` development, run the standalone script without
sudo; local Python setup logs are under `reports/setup/` in the checkout.

If normal storage is full, choose an existing writable filesystem with free space:

```bash
sudo bash scripts/diagnose.sh --output-dir /path/on/another/disk
```

Diagnostic archives exclude private keys, cloud-init user-data, workspaces, VM disks,
and environment dumps. Redaction covers common credential formats on a best-effort
basis. Review an archive before sharing it; automatic collection is not a guarantee
that every sensitive string has been recognized.

If the latest run could not write its report, use the emergency path printed in the
terminal. The normal latest-report pointer may still identify an older run.

### Rerun, update, or clean up

```bash
sudo agentlab validate
sudo agentlab cleanup
```

After pulling a code update, repeat the installer to refresh the installed copy:

```bash
git pull --ff-only
./scripts/start.sh
```

Updating the Git checkout alone does not update `/opt/generic-agent-lab`. If ownership
state is damaged, preserve it and collect diagnostics before attempting repairs.
Run cleanup successfully before manually deleting runtime records; those records
identify the resources the broker is permitted to remove.

## 10. Public repository hygiene

The repository uses generic names, a generic commit identity, an MIT license, and
documentation that describes only this infrastructure lab. The original planning
document, local reports, runtime state, keys, images, artifacts, environment files,
logs, and diagnostic archives are excluded from normal Git tracking.

[`scripts/check-public.py`](../scripts/check-public.py) checks staged file contents for
forbidden runtime/private paths and common credential patterns. It also supports
explicit private-name deny terms. This is a publication guard, not a claim that a
pattern scanner can recognize every possible secret or confidential term.

The public report links to summarized CI evidence rather than publishing local runtime
archives. CI uses restricted repository permissions and pinned action revisions.

## 11. Remaining limits and what a PASS means

The completed evidence supports running this implementation on Ubuntu 22.04 and
24.04 hosts that satisfy its prerequisites. It cannot guarantee zero errors on an
unseen machine. In particular:

- Firmware virtualization settings, nested-virtualization exposure, CPU architecture,
  RAM, and free storage depend on the host and its provider.
- Repository availability, DNS, TLS, package locks, proxies, local firewall rules,
  and libvirt network conflicts can still require an operator fix.
- Ubuntu packages and the initially selected official cloud image can change between
  clean installations. Python dependencies are hash-locked, and image/artifact
  identities are verified and recorded, but complete OS installations are not promised
  to be bit-for-bit identical.
- A normal interrupt can run cleanup. SIGKILL, power loss, or an unavailable hypervisor
  cannot guarantee immediate cleanup; the expiry mechanisms and later cleanup command
  provide recovery when the required services are available.
- The broker and lab account are trusted. This is not a hardened multi-tenant service;
  rootless containers share the host kernel, and libvirt access is powerful.
- Guest NAT allows outbound networking. Job containers have no external network.
  There is no claim of a per-job host filesystem disk quota.
- The accepted workload is the generic fixture and approved capability set. A real
  project's dependencies, arbitrary patches, custom build profiles, and production
  tests would need a separate integration and validation step.
- The recorded Ubuntu runs demonstrate full readiness on those tested hosts. The
  intended project machine still needs its own full passing report.

The practical decision point is therefore the result generated on the target host:
the tool either proves the complete real workflow there or reports the failed layer
with evidence and recovery guidance. A passing report does not silently stand in for
untested hardware, skipped VM work, or future production integration.

## 12. Related documentation and implementation references

- [Quick start and operator commands](../README.md)
- [Validation record and CI evidence](validation-status.md)
- [Ubuntu compatibility and staged recovery](ubuntu-compatibility.md)
- [Architecture, permission model, and artifact binding](architecture.md)
- [Operations, troubleshooting, and removal](operations.md)
- [Acceptance coverage](acceptance.md)
- [Remote reproduction procedure](reproduction.md)
- [Ubuntu installer](../scripts/bootstrap-ubuntu.sh), [setup logging](../scripts/setup-common.sh),
  and [standalone diagnostics](../scripts/diagnose.sh)
- [Host checks](../agentlab/host.py), [validation workflow](../agentlab/validate.py),
  [recovery guidance](../agentlab/recovery.py), and [diagnostic collector](../agentlab/diagnostics.py)
- [Sandbox implementation](../agentlab/sandbox.py), [build broker](../agentlab/build.py),
  and [VM/SSH broker](../agentlab/vm.py)
