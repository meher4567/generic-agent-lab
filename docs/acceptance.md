# Acceptance coverage

The CLI runs host verification first and saves a report even when a required capability
is unavailable. Software tests and real container checks may continue independently of
failed VM prerequisites. Missing prerequisites produce explicit failures/skips, never
a full pass.

| Requirement | Executable evidence |
|---|---|
| Non-root service and strict tool interfaces | `host.nonroot`, registry schema/unit tests |
| KVM usable | Opens `/dev/kvm`, checks API, creates/closes a VM descriptor, then real guest boot |
| libvirt/NAT/DHCP | `host.libvirt`, `host.libvirt_network`, VM address discovery |
| Rootless Podman | `host.rootless_podman`, actual container startup |
| Per-job write access | `sandbox.workspace_write` |
| Read-only protected fixture | `sandbox.protected_reference`, unchanged SHA-256 |
| Cross-job isolation | Two simultaneous sandboxes, denied read/write, unchanged other workspace |
| Offline operation | No default route, failed outbound connection, successful Git/patch/build |
| Container resource limits | Kernel cgroup values, tmpfs flags, capability and privilege checks |
| Safe file/Git operations | Traversal/link/type tests, file tools, Git init/status/show/log/diff |
| Real patch checking | Invalid patch rejection, successful `git apply --check`, sandbox application |
| Build and artifact hashing | Compile, pytest, real wheel build, SHA-256 import/revalidation |
| Build fault handling | Explicit timeout/disk-full/dependency errors with simulated flag; real broken build |
| Actual execution timeout | `sandbox.real_execution_timeout` stops the entire timed-out second sandbox |
| Stale build rejection | Select/apply next patch, old success revoked, fresh build required |
| Verified base image | Ubuntu signature and digest checks; immutable cached qcow2 base |
| Headless disposable VMs | `vm.1.deploy` through `vm.3.deploy`, job overlays, cloud-init |
| Three-VM quota | Three concurrent guests and a rejected fourth deployment |
| Automatic IP/SSH readiness | MAC-matched DHCP lease, pinned SSH host key, cloud-init marker |
| Correct guest artifact | Guest binding/hash validation, Python, writable disk, toy function smoke |
| Wrong VM/image binding | Actual mismatch rejected before SSH; cross-job unit tests |
| VM faults | No IP, SSH/boot timeout, unavailable hypervisor, stale VM, wrong image, disconnect simulations |
| Cleanup | UUID/metadata ownership checks, confirmed absence, private material removal, base retained |
| Clean public source | `.gitignore`, explicit file review, `scripts/check-public.py` |
| Fresh clone | CI installs/tests from a checkout; documented clean-clone command |
| Second-host reproduction | Separate full run required, recorded explicitly as external evidence |

No default software-only CI run is evidence of real KVM capability. The HTML report is
a local convenience; the JSON report contains the authoritative per-check details.
