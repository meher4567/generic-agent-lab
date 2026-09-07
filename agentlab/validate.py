from __future__ import annotations

import html
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

from .host import verify_host
from .models import LabError
from .policy import read_file, sha256
from .process import checked, run
from .registry import Registry
from .sandbox import SANDBOX_IMAGE, Sandbox
from .store import Store, atomic_json, new_id, now
from .vm import MAX_VMS, VMBroker


class Validation:
    def __init__(self, store: Store, profile: str, vm_count: int = 3):
        self.store, self.profile, self.vm_count = store, profile, vm_count
        self.registry = Registry(store)
        self.rows: list[dict] = []
        self.jobs: list[str] = []
        self.folder = store.root / "reports" / new_id("RUN")
        self.folder.mkdir(mode=0o700)
        self.started = now()

    def step(self, name: str, action, *, simulated: bool = False):
        print(f"[RUN ] {name}", flush=True)
        start = time.monotonic()
        done = threading.Event()

        def heartbeat():
            while not done.wait(30):
                print(f"[RUN ] {name} ({int(time.monotonic() - start)}s elapsed)", flush=True)
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            value = action()
            self.rows.append({"name": name, "status": "PASS", "evidence": value,
                              "simulated": simulated, "duration": round(time.monotonic() - start, 3)})
            print(f"[PASS] {name}", flush=True)
            return value
        except Exception as exc:
            self.rows.append({"name": name, "status": "FAIL", "evidence": str(exc),
                              "code": exc.code if isinstance(exc, LabError) else type(exc).__name__,
                              "details": exc.data if isinstance(exc, LabError) else {},
                              "duration": round(time.monotonic() - start, 3), "simulated": simulated})
            print(f"[FAIL] {name}: {exc}", flush=True)
            return None
        finally:
            done.set()
            thread.join(timeout=1)
            self.write_report()

    def skip(self, name: str, reason: str):
        self.rows.append({"name": name, "status": "SKIP", "evidence": reason, "simulated": False})
        print(f"[SKIP] {name}: {reason}", flush=True)

    def call(self, name: str, expected: str = "OK", **args) -> dict:
        result = self.registry.call(name, args)
        if result.code != expected or result.status != ("PASS" if expected == "OK" else "FAIL"):
            raise LabError(result.code if result.code != "OK" else "EXPECTED_FAILURE_MISSING",
                           result.message or f"Expected {expected}, got {result.code}", **result.data)
        return {**result.data, **({"expected_failure": expected} if expected != "OK" else {})}

    def software_tests(self):
        source = Path(os.environ.get("LAB_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
        if not (source / "tests/unit").is_dir():
            raise LabError("TESTS_MISSING", "Run from the repository or bootstrap installation")
        result = run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                      str(source / "tests/unit"), str(source / "tests/fault"),
                      "--junitxml", str(self.folder / "software-tests.xml")], timeout=180)
        (self.folder / "software-tests.log").write_text(result.output)
        if result.returncode or result.timed_out or result.output_limited:
            raise LabError("SOFTWARE_TESTS_FAILED", "Software/fault tests failed; inspect software-tests.log",
                           output=result.output[-4000:])
        return {"output": result.output[-2000:], "junit": "software-tests.xml"}

    def ensure_sandbox_image(self):
        source = Path(os.environ.get("LAB_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
        # Rebuild uses Podman's cache and notices changes to the trusted build profile.
        result = checked(["podman", "build", "--tag", SANDBOX_IMAGE, "--file",
                          str(source / "containers/Containerfile"), str(source)], timeout=1200)
        (self.folder / "sandbox-image.log").write_text(result.output)
        return json.loads(checked(["podman", "image", "inspect", SANDBOX_IMAGE]).output)[0]["Id"]

    def sandbox_script(self, job_id: str, script: str) -> dict:
        result = Sandbox(self.store).execute(job_id, ["python3", "-c", script])
        if result.returncode:
            raise LabError("SANDBOX_ASSERTION_FAILED", result.output)
        return {"output": result.output}

    def sandbox_workflow(self) -> dict | None:
        job = self.step("job.create", lambda: self.call("job_create"))
        if not job:
            return None
        job_id, source_id = job["job_id"], job["current_source_id"]
        self.jobs.append(job_id)
        args = {"job_id": job_id, "source_id": source_id}
        if self.step("sandbox.create", lambda: self.call("sandbox_create", job_id=job_id)) is None:
            return None
        self.step("sandbox.workspace_write", lambda: self.sandbox_script(job_id,
                  "from pathlib import Path; p=Path('/job/probe.txt'); p.write_text('hello'); "
                  "assert p.read_text()=='hello'; print('job write succeeded')"))
        reference = self.store.job(job_id) / "reference"
        before = sha256(read_file(reference, "reference.txt"))

        def protected():
            result = self.sandbox_script(job_id, """from pathlib import Path
for target in ['/reference/reference.txt', '/etc/lab-forbidden', '/job/../state.json']:
    try:
        Path(target).write_text('denied')
    except (PermissionError, OSError):
        pass
    else:
        raise AssertionError('Write unexpectedly allowed: ' + target)
print('reference, root filesystem, broker metadata: writes denied')
""")
            if sha256(read_file(reference, "reference.txt")) != before:
                raise LabError("REFERENCE_MODIFIED", "Protected reference hash changed")
            return {**result, "reference_sha256": before}
        self.step("sandbox.protected_reference", protected)
        other = self.step("isolation.second_job", lambda: self.call("job_create"))
        if other:
            other_id = other["job_id"]
            self.jobs.append(other_id)
            other_root = self.store.job(other_id)
            (other_root / "workspace/private.txt").write_text("other-job-only")
            self.step("isolation.second_sandbox", lambda: self.call("sandbox_create", job_id=other_id))
            script = f"""from pathlib import Path
p = Path({str(other_root / 'workspace/private.txt')!r})
for action in [lambda: p.read_text(), lambda: p.write_text('forbidden')]:
    try:
        action()
    except OSError:
        pass
    else:
        raise AssertionError('Cross-job access allowed')
assert not Path('/job/state.json').exists()
assert not Path('/run/libvirt/libvirt-sock').exists()
assert not Path('/var/run/docker.sock').exists()
print('cross-job read/write and broker socket access denied')
"""
            self.step("sandbox.cross_job_isolation", lambda: self.sandbox_script(job_id, script))
            self.step("isolation.other_workspace_unchanged", lambda: self.assertion(
                (other_root / "workspace/private.txt").read_text() == "other-job-only"))
        self.step("sandbox.offline_network", lambda: self.sandbox_script(job_id, """import socket
from pathlib import Path
assert not any(line.split()[1] == '00000000' for line in Path('/proc/net/route').read_text().splitlines()[1:])
try:
    socket.create_connection(('1.1.1.1', 443), timeout=2)
except OSError:
    print('no default route; outbound network denied')
else:
    raise AssertionError('External network unexpectedly reachable')
"""))
        self.step("sandbox.resource_limits", lambda: self.sandbox_script(job_id, """from pathlib import Path
root = Path('/sys/fs/cgroup')
assert int((root/'memory.max').read_text()) == 512 * 1024 * 1024
assert int((root/'pids.max').read_text()) == 128
quota, period = (root/'cpu.max').read_text().split()
assert int(quota) / int(period) <= 1
assert 'noexec' in next(line for line in Path('/proc/mounts').read_text().splitlines() if line.split()[1] == '/tmp')
assert 'NoNewPrivs:\t1' in Path('/proc/self/status').read_text()
assert int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')), 16) == 0
print('kernel memory/CPU/PID limits, noexec tmpfs, no-new-privileges, zero capabilities verified')
"""))
        if other:
            def real_timeout():
                sandbox = Sandbox(self.store)
                try:
                    sandbox.execute(other["job_id"], ["python3", "-c", "import time; time.sleep(15)"], timeout=1)
                except LabError as exc:
                    if exc.code != "BUILD_TIMEOUT":
                        raise
                    if sandbox.inspect(other["job_id"])["State"]["Running"]:
                        raise LabError("TIMEOUT_CLEANUP_FAILED", "Timed-out container is still running")
                    return {"expected_failure": "BUILD_TIMEOUT", "container_stopped": True, "simulated": False}
                raise LabError("EXPECTED_FAILURE_MISSING", "Container command did not time out")
            self.step("sandbox.real_execution_timeout", real_timeout)
        for path in ("/etc/passwd", "../../etc/passwd"):
            self.step(f"policy.reject_{'absolute' if path.startswith('/') else 'traversal'}",
                      lambda p=path: self.call("file_read", expected="PATH_DENIED", job_id=job_id, path=p))
        self.step("file.read", lambda: self.call("file_read", job_id=job_id, path="source/src/toy_lab/__init__.py"))
        self.step("file.list", lambda: self.call("file_list", job_id=job_id, path="source"))
        self.step("file.find", lambda: self.call("file_find", job_id=job_id, query="def add"))
        if self.step("git.init", lambda: self.call("git_init", **args)) is None:
            return None
        for tool in ("git_status", "git_show", "git_log", "git_diff"):
            self.step(tool.replace("_", "."), lambda t=tool: self.call(t, **args))
        self.step("build.broken_source_rejected", lambda: self.call("build_run", expected="BUILD_FAILED", **args))
        invalid = self.step("patch.invalid_fixture", lambda: self.call("patch_create", variant="invalid", **args))
        if invalid:
            self.step("patch.invalid_rejected", lambda: self.call("patch_check", expected="PATCH_REJECTED",
                                                                 patch_id=invalid["patch_id"], **args))
        patch = self.step("patch.create", lambda: self.call("patch_create", **args))
        if not patch:
            return None
        patch_args = {**args, "patch_id": patch["patch_id"]}
        if self.step("patch.check", lambda: self.call("patch_check", **patch_args)) is None:
            return None
        if self.step("patch.apply", lambda: self.call("patch_apply", **patch_args)) is None:
            return None
        if self.step("build.compile_test_package", lambda: self.call("build_run", **args)) is None:
            return None
        self.step("build.hash_verified", lambda: self.call("build_status", job_id=job_id))
        next_patch = self.step("lineage.select_next_patch", lambda: self.call("patch_create", variant="next", **args))
        self.step("lineage.old_build_rejected", lambda: self.call("build_status", expected="STALE_BUILD", job_id=job_id))
        if not next_patch:
            return None
        self.step("lineage.new_patch_not_run", lambda: self.assertion(
            self.store.state(job_id).build_status == "NOT_RUN"))
        self.step("lineage.apply_next_patch", lambda: self.call("patch_apply", patch_id=next_patch["patch_id"], **args))
        for fault in ("BUILD_TIMEOUT", "DISK_FULL", "DEPENDENCY_FAILURE"):
            def inject(f=fault):
                try:
                    self.call("fault_set", job_id=job_id, fault=f)
                    return self.call("build_run", expected=f, **args)
                finally:
                    self.call("fault_clear", job_id=job_id)
            self.step(f"fault.{fault.lower()}", inject, simulated=True)
        build = self.step("build.recover_after_faults", lambda: self.call("build_run", **args))
        return {"job_id": job_id, "build": build} if build else None

    @staticmethod
    def assertion(condition: bool):
        if not condition:
            raise LabError("ASSERTION_FAILED", "Expected condition was false")
        return {"verified": True}

    def vm_workflow(self, job_id: str):
        if not self.step("image.signed_base_download", VMBroker(self.store).prepare_base):
            return
        image = self.step("image.register_build_binding", lambda: self.call("image_register", job_id=job_id))
        if not image:
            return
        image_id = image["image_id"]
        machines = []
        for index in range(self.vm_count):
            vm = self.step(f"vm.{index+1}.deploy", lambda: self.call("vm_deploy", job_id=job_id, image_id=image_id))
            if vm:
                machines.append(vm)
        if len(machines) != self.vm_count:
            self.skip("vm.concurrent_capacity", "One or more VM deployments failed")
        else:
            self.step("vm.concurrent_capacity", lambda: {"running_vms": len(machines), "requested": self.vm_count})
        if len(machines) == MAX_VMS:
            self.step("vm.quota_enforced", lambda: self.call("vm_deploy", expected="VM_QUOTA", job_id=job_id, image_id=image_id))
        else:
            self.skip("vm.quota_enforced", "Real three-VM quota boundary requires --vm-count 3")
        for index, vm in enumerate(machines, 1):
            args = {"job_id": job_id, "vm_id": vm["vm_id"]}
            ready = self.step(f"vm.{index}.dhcp_ssh_cloud_init", lambda a=args: self.call("vm_wait", **a))
            if ready:
                self.step(f"vm.{index}.profile_limits", lambda r=ready: self.assertion(
                    r["ram_mib"] == 1536 and r["vcpus"] == 1))
                self.step(f"vm.{index}.guest_smoke_artifact", lambda a=args:
                          self.call("test_run", image_id=image_id, **a))
        if machines:
            args = {"job_id": job_id, "vm_id": machines[0]["vm_id"]}
            wrong = "IMAGE-" + ("0" * 64 if image_id != "IMAGE-" + "0" * 64 else "1" * 64)
            self.step("lineage.wrong_vm_image_rejected", lambda: self.call("test_run",
                      expected="WRONG_IMAGE_BINDING", image_id=wrong, **args))
            self.step("lineage.stale_vm_rejected", lambda: self.call("vm_status", expected="STALE_VM_ID",
                      job_id=job_id, vm_id="VM-" + "0" * 32))
            for fault in ("NO_IP", "SSH_TIMEOUT", "BOOT_TIMEOUT", "STALE_VM_ID",
                          "WRONG_IMAGE_BINDING", "SSH_DISCONNECT", "HYPERVISOR_UNAVAILABLE"):
                def inject(f=fault):
                    try:
                        self.call("fault_set", job_id=job_id, fault=f)
                        return self.call("vm_wait", expected=f, **args)
                    finally:
                        self.call("fault_clear", job_id=job_id)
                self.step(f"fault.{fault.lower()}", inject, simulated=True)

    def cleanup(self):
        vm = VMBroker(self.store)
        for job_id in self.jobs:
            for record in vm.records():
                if record["job_id"] == job_id:
                    self.step(f"cleanup.{record['vm_id']}", lambda r=record:
                              self.call("vm_destroy", job_id=r["job_id"], vm_id=r["vm_id"]))
            self.step(f"cleanup.sandbox.{job_id}", lambda j=job_id: self.call("sandbox_destroy", job_id=j))

    def write_report(self, finished: bool = False) -> dict:
        failed = any(r["status"] == "FAIL" for r in self.rows)
        skipped = any(r["status"] == "SKIP" for r in self.rows)
        ready = finished and self.profile == "full" and self.vm_count == 3 and not failed and not skipped
        status = "RUNNING" if not finished else "FAIL" if failed else "PASS" if ready else "PARTIAL"
        report = {"schema_version": 1, "run_id": self.folder.name, "started_at": self.started,
                  "finished_at": now() if finished else None, "profile": self.profile,
                  "requested_vm_count": self.vm_count, "status": status, "environment_ready": ready,
                  "second_host_reproduction": "NOT_VERIFIED_BY_THIS_RUN",
                  "checks": self.rows, "jobs": self.jobs}
        atomic_json(self.folder / "report.json", report)
        summary = [f"# Lab validation: {status}", "", f"Environment ready: **{ready}**",
                   f"Profile: `{self.profile}`; VM count: {self.vm_count}", "",
                   "A separate run on a second host is required to prove remote reproduction.", "",
                   "| Check | Result | Evidence / remediation |", "|---|---|---|"]
        table_rows = []
        for row in self.rows:
            detail = str(row.get("evidence", "")) + (" " + row["remediation"] if row.get("remediation") else "")
            summary.append(f"| {row['name']} | {row['status']} | {detail[:1000].replace('|', '/').replace(chr(10), ' ')} |")
            table_rows.append(f"<tr><td>{html.escape(row['name'])}</td><td class='{row['status']}'>{row['status']}</td>"
                              f"<td><pre>{html.escape(detail[:4000])}</pre></td></tr>")
        (self.folder / "summary.md").write_text("\n".join(summary) + "\n")
        document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Lab validation: {status}</title>
<style>body{{font:16px system-ui;margin:3rem auto;max-width:1200px;padding:0 1rem;color:#182330;background:#f6f8fb}}
h1{{font-size:2.5rem}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{padding:1rem;text-align:left;border-bottom:1px solid #dde3ec}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px ui-monospace,monospace;margin:0}}.PASS{{color:#08784b}}.FAIL{{color:#b42318}}.SKIP,.WARN{{color:#8a5500}}td:first-child{{font-weight:600}}</style>
<h1>Lab validation: {status}</h1><p>Environment ready: <strong>{ready}</strong> · Profile: {self.profile} · VMs: {self.vm_count}</p>
<p>Real environment checks and simulated fault checks are recorded separately in report.json. A second host run is required to prove remote reproduction.</p>
<table><thead><tr><th>Check</th><th>Result</th><th>Evidence / next action</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table></html>"""
        (self.folder / "report.html").write_text(document)
        atomic_json(self.store.root / "reports/latest.json", {"run_id": self.folder.name, "status": status,
                                                            "environment_ready": ready})
        return report

    def run(self) -> tuple[dict, int]:
        previous = signal.getsignal(signal.SIGTERM)

        def interrupted(signum, frame):
            raise KeyboardInterrupt("Validation interrupted")
        signal.signal(signal.SIGTERM, interrupted)
        try:
            checks = verify_host(self.store, self.profile, self.vm_count,
                                 on_check=lambda r: print(f"[{r['status']:4}] {r['name']}: {r['evidence']}", flush=True))
            self.rows.extend(checks)
            software = self.step("software.unit_and_fault_tests", self.software_tests)
            sandbox_names = {"host.nonroot", "host.python", "host.podman", "host.git",
                             "host.rootless_podman", "host.subuid", "host.subgid",
                             "host.sandbox_disk", "host.sandbox_memory"}
            sandbox_ok = not any(r["status"] == "FAIL" and r["name"] in sandbox_names for r in checks)
            workflow = None
            if self.profile != "software" and sandbox_ok and software:
                if self.step("sandbox.image_build", self.ensure_sandbox_image):
                    workflow = self.sandbox_workflow()
            else:
                self.skip("sandbox.workflow", "Profile excludes containers or prerequisites failed")
            full_host_ok = not any(r["status"] == "FAIL" for r in checks)
            if self.profile == "full" and full_host_ok and workflow:
                self.vm_workflow(workflow["job_id"])
            else:
                self.skip("vm.workflow", "Profile excludes VMs or full host/build prerequisites failed")
        except KeyboardInterrupt:
            self.rows.append({"name": "validation.interrupted", "status": "FAIL", "evidence": "Interrupted"})
        except Exception as exc:
            self.rows.append({"name": "validation.error", "status": "FAIL", "evidence": str(exc)})
        finally:
            try:
                self.cleanup()
            except Exception as exc:
                self.rows.append({"name": "cleanup.error", "status": "FAIL", "evidence": str(exc)})
            signal.signal(signal.SIGTERM, previous)
        report = self.write_report(finished=True)
        code = 1 if report["status"] == "FAIL" else 0
        if self.profile == "full" and not report["environment_ready"]:
            code = 1
        print(f"\n{report['status']} · environment_ready={report['environment_ready']}\nReports: {self.folder}", flush=True)
        return report, code
