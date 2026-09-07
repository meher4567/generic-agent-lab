# Validation status

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
| Full three-VM acceptance on a suitably sized Ubuntu host | Requires the target-host run |
| Second-host full reproduction | Requires a separate target host |

The single-guest development test is supplementary evidence. Its report is deliberately
`PARTIAL`, not an assertion that the full Ubuntu environment is ready. The development
host does not meet the documented full acceptance OS/resource requirements.

GitHub Actions separately runs the Ubuntu installer, checks that installation can be
repeated, and runs the real rootless sandbox suite. Consult the workflow result for the
exact commit being reviewed. Hosted CI does not substitute for full three-VM validation.

Local detailed reports and keys are excluded from the repository. Run `scripts/start.sh`
on the target Ubuntu host to generate your own evidence and readiness decision.
