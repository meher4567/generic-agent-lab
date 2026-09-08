# Generic Agent Lab

One command to install an Ubuntu lab and find out whether the environment actually works.

This project tests infrastructure for a future engineering agent using a harmless Python
fixture. It includes no model, API key requirement, production source, or product-specific workload.

The full Ubuntu workflow has passed 101 checks with three real KVM guests on each of
two separate Ubuntu runner instances. See the
[validation record](docs/validation-status.md) for the tested revision and CI evidence.

## Start on a clean Ubuntu host

Use **Ubuntu 22.04 LTS or 24.04 LTS, x86_64**, with hardware or nested virtualization enabled.
See [Ubuntu compatibility and staged recovery](docs/ubuntu-compatibility.md) for release
differences and the fallback sequence. The full acceptance profile requires
at least 4 logical CPUs, approximately 16 GB RAM, 80 GiB free on both the runtime and VM
storage filesystems, and approximately 5.5 GiB currently available RAM for three guests.
If both paths share one filesystem, the 80 GiB requirement is not additive.

```bash
sudo apt-get update
sudo apt-get install -y git
git clone https://github.com/meher4567/generic-agent-lab.git
cd generic-agent-lab
./scripts/start.sh
```

Enter your sudo password when the OS requests it. The installer then:

1. Installs the Ubuntu packages and hash-locked Python dependencies.
2. Creates the dedicated `agentlab` account with libvirt/KVM access and subordinate IDs.
3. Starts libvirt, the default NAT/DHCP network, and a user session for rootless Podman.
4. Installs the CLI in `/opt/generic-agent-lab` and a VM expiry timer.
5. Runs host checks, software/fault tests, the real sandbox workflow, and three real VMs.
6. Runs guest tests over SSH, cleans up its VMs/containers, and saves JSON, HTML, and Markdown reports.

Host setup uses root; the controller, builds, containers, and VM broker run as `agentlab`.
The lab account is not granted sudo access. Existing unrelated containers and VMs are retained.
Downloads happen during setup/image preparation. Job sandboxes have no external network.
The first run needs internet access to Ubuntu package/image servers and the Python package index.

## Read the result

```bash
sudo agentlab report
sudo agentlab report --failures
sudo agentlab doctor
```

Reports are stored in `/home/agentlab/.local/share/generic-agent-lab/reports/RUN-…/`:

| File | Purpose |
|---|---|
| `report.html` | Open in a browser for the check-by-check result and next actions |
| `report.json` | Machine-readable evidence, statuses, simulation flags, and job IDs |
| `summary.md` | Text report suitable for local review |
| `software-tests.xml` | JUnit test evidence |
| `software-tests.log` | Software/fault test output |

`PASS` with `environment_ready: true` means the complete three-VM profile passed on
**this host**. A required failure or skipped full check produces a non-zero exit status.
`PARTIAL` is a scoped result; it never claims the full environment is ready. Controlled
faults pass only when the expected error is returned, and their evidence is marked
`simulated: true`. A missing VM capability is never silently replaced with a simulated VM.

The installer logs failures, including the failed phase, under `/var/log/generic-agent-lab/`.
An unsupported OS is rejected before host changes. BIOS settings, nested virtualization,
additional memory/disk, and a second physical/remote host cannot be supplied by the installer.

To copy the latest reports into your working directory:

```bash
mkdir -p reports
latest=$(sudo agentlab report | python3 -c 'import json,sys; print(json.load(sys.stdin)["html"])')
sudo cp -r "$(dirname "$latest")" reports/
sudo chown -R "$(id -u):$(id -g)" reports/
```

Reports are private runtime evidence and are ignored by Git.

## If a run fails

Start with the **first failed check** printed at the end of the run. The HTML report
puts failures and recovery commands before the full check table. `report --failures`
shows only failed/skipped checks, their error codes, evidence, and next actions.

```bash
sudo agentlab report --failures
sudo agentlab diagnose
```

`diagnose` creates a private local `.tar.gz` bundle and prints its path. It collects
bounded report/build/error log excerpts, disk/inode/memory checks, versions, and service
status. It excludes keys, cloud-init user-data, workspaces, VM disks, and environment
dumps. It redacts common credential formats; review the contents before sharing.
Nothing is uploaded automatically. Unavailable evidence is listed in the bundle manifest.

If installation failed before `agentlab` became available, run from the clone:

```bash
sudo bash scripts/diagnose.sh
```

This fallback uses standard-library Python and can read root-owned bootstrap logs.
For a failed local `--software`/`--sandbox` setup, use `bash scripts/diagnose.sh` without
sudo; Python setup logs are in `reports/setup/`. Add `--output-dir /path/on/another/disk`
to either diagnostic command if the normal disk is full.

Setup waits up to 120 seconds for apt's package lock and retries package downloads
three times. An interrupted or explicitly failed setup still saves its phase and exit
code. Concurrent setup is rejected with a clear message; busy broker locks time out
after 30 seconds. Repeating the original start command is the recovery path after the
reported cause is resolved. Failed builds and VM creation are not blindly retried.

On a slow host, guest startup can be given up to ten minutes:

```bash
sudo agentlab validate --vm-timeout 600
# Or during installation:
./scripts/start.sh --vm-timeout 600
```

A guest readiness failure saves the last state/IP/SSH output and, when SSH is reachable,
cloud-init diagnostics before cleanup. Readiness uses two stages within the configured
time budget; only temporary DHCP/SSH/cloud-init timeouts get a second attempt. Identity
mismatches and stopped guests fail immediately. If writing reports fails, cleanup still runs
and the tool attempts an emergency JSON report under `/tmp`; its exact path is printed.
If both locations are unavailable, preserve the terminal output. The normal latest
report pointer can still refer to an older run in that situation.

## Rerun and clean up

```bash
sudo agentlab validate                  # Full proof again; preserves earlier evidence
sudo agentlab cleanup                   # Only resources owned by this lab installation
sudo agentlab cleanup --job-id JOB-…     # Only one registered job
./scripts/start.sh                      # Repeat installation/validation after a fresh OS setup
```

Each run uses new job IDs. Cleanup is idempotent, verifies absence, removes per-VM
private keys and overlay/seed disks, and retains the immutable base image and reports.
A timer checks VM lifetimes every minute; guests also schedule shutdown after one hour.
Use cleanup before deleting the runtime directory: its ownership records are needed to
identify the resources safely. See [operations](docs/operations.md) for recovery and removal.

## Smaller checks on a development machine

```bash
./scripts/start.sh --software  # Python setup, unit tests, and simulated faults
./scripts/start.sh --sandbox   # Also builds/runs real rootless Podman sandboxes
./scripts/start.sh --check     # Full validation under an already provisioned current user
```

These local modes install Python dependencies into `.venv` and use the current non-root
account. They do not install OS packages. `--sandbox` requires working rootless Podman,
cgroups v2, subordinate IDs, 3 GiB free disk, and 700 MiB available RAM. Other Linux
distributions may pass these scoped tests; they do not prove the Ubuntu bootstrap.

`./scripts/start.sh --vm-count 1` performs a smaller Ubuntu VM run while retaining the
full host requirements. Its result is `PARTIAL` with exit code 1 because the three-VM
capacity/quota acceptance test has not passed.

## Manual tool workflow

The `agentlab` operator wrapper runs `labctl` as the dedicated account. For a local
development install, replace `sudo agentlab` with `.venv/bin/labctl`.

```bash
sudo agentlab job create
sudo agentlab sandbox create JOB-ID
sudo agentlab git init JOB-ID SOURCE-ID
sudo agentlab file read JOB-ID source/src/toy_lab/__init__.py
sudo agentlab git show JOB-ID SOURCE-ID
sudo agentlab patch create JOB-ID SOURCE-ID
sudo agentlab patch check JOB-ID SOURCE-ID PATCH-ID
sudo agentlab patch apply JOB-ID SOURCE-ID PATCH-ID
sudo agentlab build run JOB-ID SOURCE-ID
sudo agentlab image prepare
sudo agentlab image register JOB-ID
sudo agentlab vm deploy JOB-ID IMAGE-ID
sudo agentlab vm wait JOB-ID VM-ID
sudo agentlab test run JOB-ID VM-ID IMAGE-ID
sudo agentlab vm destroy JOB-ID VM-ID
sudo agentlab sandbox destroy JOB-ID
```

Replace each placeholder with the actual ID returned by the earlier command.
`labctl tools` prints strict JSON schemas for every capability. `labctl call TOOL JSON`
is the programmatic entry point. See [architecture](docs/architecture.md) for the
permission matrix, ownership model, artifact binding, and limitations.

## Development and validation coverage

```bash
./scripts/setup-python.sh
.venv/bin/pytest -q tests/unit tests/fault
.venv/bin/ruff check agentlab containers scripts tests
shellcheck scripts/*.sh
podman build -t localhost/generic-agent-lab-sandbox:0.1.0 -f containers/Containerfile .
LAB_RUN_INTEGRATION=1 .venv/bin/pytest -q tests/integration/test_sandbox.py
```

Real KVM pytest coverage is opt-in with `LAB_RUN_KVM=1`. The regular full CLI validation
already performs those real VM checks. Hosted CI checks software and real containers,
then attempts the full three-VM validation only when every host prerequisite passes.
A runner without those capabilities retains a scoped `PARTIAL` result.

The Python dependencies are pinned with hashes in `requirements.lock`; `uv.lock` is the
maintainer lock. The official cloud image is signature-checked with Ubuntu's packaged
keyring and SHA-256 checked, then cached by digest. Container image IDs and build
artifact digests are recorded. Ubuntu apt updates and the initial cloud image selection
can change between clean installations: this is repeatable behavior, not a promise of
bit-for-bit identical OS images.

See [acceptance coverage](docs/acceptance.md) and [remote reproduction](docs/reproduction.md).
