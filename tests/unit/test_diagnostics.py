import errno
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentlab.cli import app
from agentlab.diagnostics import collect, read_tail, redact
from agentlab.models import CommandResult, LabError
from agentlab.sandbox import Sandbox
from agentlab.store import atomic_json
from agentlab.validate import Validation
from agentlab.vm import VMBroker


def test_cli_corrupt_runtime_returns_actionable_error_without_terminal_traceback(store, monkeypatch):
    monkeypatch.setenv("LAB_ROOT", str(store.root))
    (store.root / "installation.json").write_text("not-json")
    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert data["code"] == "INVALID_STATE" and data["commands"]
    assert Path(data["log"]).is_file()
    assert "Traceback" not in result.stdout


def test_diagnostic_bundle_handles_corruption_excludes_keys_and_redacts(store, job, monkeypatch, tmp_path):
    run_id = "RUN-" + "a" * 32
    folder = store.root / "reports" / run_id
    folder.mkdir()
    atomic_json(store.root / "reports/latest.json", {"run_id": run_id})
    atomic_json(folder / "report.json", {"checks": [{"log": "failure-1.log"}], "jobs": [job["job_id"]],
                                        "access_token": "sensitive-test-value"})
    (folder / "failure-1.log").write_text("https://tester:password@example.invalid/path\nTOKEN=hidden-value")
    (store.job(job["job_id"]) / "logs/allowed.log").write_text("useful build evidence")
    (store.job(job["job_id"]) / "vm/client_key").write_text("never-include-this-private-file")
    (store.root / "installation.json").write_text("broken state")
    monkeypatch.setattr("agentlab.diagnostics.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, b"diagnostic fixture", b""))
    bundle = collect(store.root, tmp_path / "output")
    archive = Path(bundle["bundle"])
    assert archive.stat().st_mode & 0o777 == 0o600
    with tarfile.open(archive) as handle:
        assert all(m.isfile() and m.size <= 2 * 1024**2 for m in handle.getmembers())
        contents = "\n".join(handle.extractfile(m).read().decode() for m in handle.getmembers())
        report = json.load(handle.extractfile("report.json"))
    assert report["access_token"] == "[REDACTED]"
    assert "useful build evidence" in contents
    for forbidden in ("tester:password", "hidden-value", "sensitive-test-value", "never-include-this-private-file"):
        assert forbidden not in contents


def test_diagnostic_reader_rejects_symlinks_hardlinks_and_fifo(tmp_path):
    (tmp_path / "original").write_text("private")
    (tmp_path / "symlink").symlink_to(tmp_path / "original")
    os.link(tmp_path / "original", tmp_path / "hardlink")
    os.mkfifo(tmp_path / "fifo")
    for name in ("symlink", "hardlink", "fifo"):
        with pytest.raises((OSError, ValueError)):
            read_tail(tmp_path, name)
    (tmp_path / "parent").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        read_tail(tmp_path, "parent/original")


def test_diagnostic_reader_bounds_large_output_and_private_key_redaction(tmp_path):
    (tmp_path / "large").write_text("a" * 100_000)
    result = read_tail(tmp_path, "large", 200)
    assert result.endswith("a" * 200) and len(result) < 250
    kind = "OPENSSH"
    text = f"-----BEGIN {kind} PRIVATE KEY-----\nfixture\n-----END {kind} PRIVATE KEY-----"
    assert "fixture" not in redact(text)


def test_no_latest_report_still_produces_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setattr("agentlab.diagnostics.subprocess.run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, b"", b"unavailable"))
    result = collect(tmp_path / "missing-runtime", tmp_path / "output")
    assert Path(result["bundle"]).exists() and result["omissions"]


def test_logging_failure_preserves_original_error_and_allows_cleanup(store, job, monkeypatch):
    monkeypatch.setattr("agentlab.validate.verify_host", lambda *a, **k: [])

    def fail():
        raise LabError("BUILD_FAILED", "original failure")

    cleaned = []
    monkeypatch.setattr(Sandbox, "destroy", lambda self, job_id: cleaned.append(job_id) or {"verified_absent": True})
    validation = Validation(store, "software")
    validation.jobs = [job["job_id"]]
    validation.software_tests = fail
    real_atomic = atomic_json

    def full_disk(path, data):
        if str(path).startswith(str(store.root / "reports")):
            raise OSError(errno.ENOSPC, "No space left on device")
        real_atomic(path, data)

    monkeypatch.setattr("agentlab.validate.atomic_json", full_disk)
    report, code = validation.run()
    assert code == 1 and cleaned == [job["job_id"]]
    assert report["first_failure"] == "software.unit_and_fault_tests"
    assert report["checks"][0]["code"] == "BUILD_FAILED"
    assert any(r["code"] == "REPORT_WRITE_FAILED" for r in report["checks"] if "code" in r)
    assert json.loads(Path(validation.emergency_report).read_text())["environment_ready"] is False


def test_corrupt_vm_record_does_not_prevent_sandbox_cleanup(store, vm_record, monkeypatch):
    folder = store.job(vm_record["job_id"]) / "vm" / vm_record["vm_id"]
    (folder / "record.json").write_text("invalid json")
    cleaned = []
    monkeypatch.setattr(Sandbox, "destroy", lambda self, job_id: cleaned.append(job_id) or {"verified_absent": True})
    validation = Validation(store, "software")
    validation.jobs = [vm_record["job_id"]]
    validation.cleanup()
    assert cleaned == [vm_record["job_id"]]
    assert validation.rows[0]["status"] == "FAIL" and validation.rows[1]["status"] == "PASS"


def test_vm_timeout_preserves_cloud_init_and_ssh_evidence(store, vm_record, monkeypatch):
    broker = VMBroker(store)
    monkeypatch.setattr(broker, "status", lambda *a: {"state": "running", "ip": "192.168.122.2"})
    monkeypatch.setattr(broker, "ssh_args", lambda *a: ["ssh", "fixture"])
    calls = []

    def command(argv, **kwargs):
        calls.append(argv[-1])
        return CommandResult(returncode=1, output="cloud-init fixture failed", duration=0)

    monkeypatch.setattr("agentlab.vm.run", command)
    with pytest.raises(LabError) as error:
        broker.wait(vm_record["job_id"], vm_record["vm_id"], timeout=0.02)
    assert error.value.code == "BOOT_TIMEOUT"
    assert error.value.data["saw_ssh"] is True and len(calls) == 2
    saved = json.loads(Path(error.value.data["log_path"]).read_text())
    assert saved["guest_diagnostics"]["output"] == "cloud-init fixture failed"


def test_stopped_vm_fails_immediately(store, vm_record, monkeypatch):
    broker = VMBroker(store)
    monkeypatch.setattr(broker, "status", lambda *a: {"state": "shut off", "ip": None})
    with pytest.raises(LabError) as error:
        broker.wait(vm_record["job_id"], vm_record["vm_id"], timeout=240)
    assert error.value.code == "VM_STOPPED" and error.value.data["saw_ip"] is False


def test_busy_lock_times_out_then_can_be_reused(store):
    with store.lock("test-resource"):
        with pytest.raises(LabError) as error:
            with store.lock("test-resource", timeout=0.02):
                pytest.fail("Competing lock unexpectedly acquired")
    assert error.value.code == "RESOURCE_BUSY"
    with store.lock("test-resource", timeout=0.02):
        pass


def test_timeout_is_preserved_when_container_stop_fails(store, job, monkeypatch):
    sandbox = Sandbox(store)
    monkeypatch.setattr(sandbox, "inspect", lambda *a: {"State": {"Running": True}})
    monkeypatch.setattr("agentlab.sandbox.run", lambda *a, **k:
                        CommandResult(returncode=124, output="", duration=0))

    def stop(*a, **k):
        raise LabError("PODMAN_UNAVAILABLE", "cannot stop container")

    monkeypatch.setattr("agentlab.sandbox.checked", stop)
    with pytest.raises(LabError) as error:
        sandbox.execute(job["job_id"], ["fixture"])
    assert error.value.code == "BUILD_TIMEOUT" and error.value.data["cleanup_error"]


def test_event_write_failure_reports_completed_operation(store, job, registry, monkeypatch):
    def no_space(*a, **k):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(store, "event", no_space)
    result = registry.call("fault_set", {"job_id": job["job_id"], "fault": "BUILD_TIMEOUT"})
    assert result.code == "EVENT_LOG_FAILED" and result.data["operation_completed"] is True
    assert store.state(job["job_id"]).fault == "BUILD_TIMEOUT"
    result = registry.call("build_run", {"job_id": job["job_id"], "source_id": job["current_source_id"]})
    assert result.code == "BUILD_TIMEOUT" and result.data["event_logging_error"]


def test_build_persistence_failure_does_not_replace_original_fault(store, job, registry, monkeypatch):
    registry.call("fault_set", {"job_id": job["job_id"], "fault": "BUILD_TIMEOUT"})

    def no_space(*a, **k):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr("agentlab.build.atomic_json", no_space)
    result = registry.call("build_run", {"job_id": job["job_id"], "source_id": job["current_source_id"]})
    assert result.code == "BUILD_TIMEOUT" and result.data["persistence_errors"]


@pytest.mark.parametrize(("command", "exit_code"), [("exit 17", 17), ("false", 1), ('kill -s TERM "$$"', 143)])
def test_setup_records_explicit_exit_command_failure_and_signal(tmp_path, command, exit_code):
    common = Path(__file__).resolve().parents[2] / "scripts/setup-common.sh"
    script = 'set -Eeuo pipefail\nsource "$1"\nsetup_logging bootstrap "$2"\nsetup_phase packages\n' + command
    result = subprocess.run(["bash", "-c", script, "test", str(common), str(tmp_path / "logs")],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == exit_code
    status = json.loads((tmp_path / "logs/bootstrap.json").read_text())
    assert status["status"] == "FAIL" and status["exit_code"] == exit_code and status["phase"] == "packages"
    assert "Collect diagnostics:" in result.stdout
    assert (tmp_path / "logs" / status["log_file"]).stat().st_mode & 0o777 == 0o600
