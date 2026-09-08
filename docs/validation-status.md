# Validation status

## Ubuntu 22.04 compatibility and staged recovery

Commit `ddef475c741d58f97ccf5399d0e861b6658e5210` passed the
[Ubuntu 22.04 / 24.04 workflow](https://github.com/meher4567/generic-agent-lab/actions/runs/34184097989).

| Host | Runtime | Full result | Real concurrent guests |
|---|---|---|---|
| Ubuntu 22.04 | Rootless Podman 3.4.4, overlay, CPU/memory/PID controllers available | 101 PASS, 0 FAIL, 0 SKIP, 0 WARN; `environment_ready: true` | 3 |
| Ubuntu 24.04 | Rootless Podman 4.9.3, overlay, CPU/memory/PID controllers available | 101 PASS, 0 FAIL, 0 SKIP, 0 WARN; `environment_ready: true` | 3 |

Both hosts passed the actual installer, real sandbox workflow, guest artifact smoke
tests, diagnostic collection, owned-resource cleanup, and repeat installation. The
validation run IDs are `RUN-273521311d1147b4b51c825cf5e901ab` (22.04) and
`RUN-22c19e2ab32f4373b485920f731a2559` (24.04).

The software matrix passed 82 tests on Python 3.10, 3.12, and 3.13. Additional tests
cover older Podman cleanup flags, missing CPU delegation, transient readiness recovery
within a shared time budget, permanent errors that must not retry, and hardware-profile
fallback with older osinfo catalogs. Both real hosts selected the available `ubuntu24.04`
hardware profile; the `ubuntu22.04` catalog fallback was verified in regression tests.
The local real Podman integration suite also passed (2 tests).

See [Ubuntu compatibility and staged recovery](ubuntu-compatibility.md) for the supported
release scope and automatic versus operator-assisted recovery paths.

## Error handling update

Commit `576aa3a3603ca2f45402452dea6618a17d82c465` passed the
[Ubuntu validation workflow](https://github.com/meher4567/generic-agent-lab/actions/runs/34158445245):

- 68 software/fault tests on Python 3.10, 3.12, and 3.13.
- Actual Ubuntu installation, rootless sandbox validation, and clean repeat installation.
- 101 full-profile checks: all PASS, zero failures, skips, or warnings; three real
  concurrent KVM guests and `environment_ready: true`.
- Both the installed diagnostic command and standalone setup-failure diagnostic collector.
- Local real Podman integration tests: 2 passed; the full local sandbox workflow also passed.

The additional regression tests cover interrupted/explicitly failed setup, corrupt
state, full-disk report persistence, emergency report fallback, busy locks, VM readiness
evidence, failed container shutdown, event/build logging failures, cleanup with corrupt
records, already-cleaned VM history with an unavailable hypervisor, and diagnostic
redaction/file exclusions. The standalone collector was also exercised with third-party
Python packages disabled.

## Initial clean-host reproduction evidence

Implementation validation performed on 2026-09-08:

| Scope | Result |
|---|---|
| Software and controlled fault tests | 51 passed |
| Real rootless Podman integration tests | 2 passed |
| Real offline sandbox / Git / patch / wheel build | Passed |
| Protected reference and cross-job denial | Passed |
| Kernel resource-limit checks and actual timeout shutdown | Passed |
| Official Ubuntu cloud-image signature and digest verification | Passed |
| One real headless Ubuntu KVM guest on a development Linux host | Passed |
| Automatic DHCP, pinned SSH, cloud-init readiness | Passed |
| Guest image/artifact binding and toy smoke suite | Passed |
| Controlled VM faults and actual wrong-binding rejection | Passed |
| Owned VM/container cleanup and base preservation | Passed |
| Fresh public Git clone, local setup command, and software tests | Passed |
| Actual Ubuntu installer and clean repeat installation | Passed on Ubuntu 24.04 CI |
| Full three-VM acceptance on a suitably sized Ubuntu host | 101 checks passed; no failures or skips |
| Second-host full reproduction | Passed on two separate fresh Ubuntu 24.04 hosted runner instances at the same commit |

The [full Ubuntu validation run](https://github.com/meher4567/generic-agent-lab/actions/runs/34156087327)
tested commit `35c09ada08df48dc88508c3a22663c1ba0593039` from a fresh checkout.
Its report records `profile: full`, `requested_vm_count: 3`, `status: PASS`, and
`environment_ready: true`. All three guests ran concurrently, obtained DHCP addresses,
completed pinned-host-key SSH/cloud-init readiness, and passed the real artifact smoke
suite. A fourth VM was rejected by the quota, and all three guests were removed with
cleanup verified. Controlled fault checks are separately marked as simulated.

The same workflow also passed the installer rerun with stale-source removal, real
rootless sandbox validation, and 51 software/fault tests on Python 3.10, 3.12, and 3.13.

The [second clean Ubuntu run](https://github.com/meher4567/generic-agent-lab/actions/runs/34156558776)
used the identical commit and a different hosted worker instance. It independently
installed the lab and passed all 101 full-profile checks, including three concurrent
real guests, guest smoke suites, cleanup, and the repeated installation.

| Evidence | First Ubuntu instance | Second Ubuntu instance |
|---|---|---|
| GitHub Actions run | `34156087327` | `34156558776` |
| Validation run | `RUN-a531a8f199e84e29899d098f78b5ae73` | `RUN-70fbb2aea5d4409e91ffd31d70c7a877` |
| Result | `PASS`, `environment_ready: true` | `PASS`, `environment_ready: true` |
| Checks | 101 PASS, 0 FAIL, 0 SKIP | 101 PASS, 0 FAIL, 0 SKIP |
| Real concurrent guests | 3 | 3 |

This is remote reproduction evidence on two hosted Ubuntu instances. Each individual
report still records `second_host_reproduction: NOT_VERIFIED_BY_THIS_RUN`; the comparison
above supplies the external evidence. Your own target machine must pass its own run.

The single-guest development test is supplementary evidence. Its report is deliberately
`PARTIAL`, not an assertion that the full Ubuntu environment is ready. The development
host does not meet the documented full acceptance OS/resource requirements.

Future GitHub Actions runs attempt the full three-VM profile when the runner's host
checks pass. Otherwise that run's VM scope remains unverified; a green software or
sandbox job alone does not prove full readiness. Consult the report for the exact run
and commit being reviewed.

Local detailed reports and keys are excluded from the repository. Run `scripts/start.sh`
on the target Ubuntu host to generate your own evidence and readiness decision.
