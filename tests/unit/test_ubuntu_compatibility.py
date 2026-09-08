import json

import pytest

from agentlab.host import verify_host
from agentlab.models import CommandResult, LabError
from agentlab.sandbox import Sandbox
from agentlab.validate import Validation
from agentlab.vm import VMBroker


@pytest.mark.parametrize("controllers,expected", [(["memory", "pids"], "FAIL"), (["cpu", "memory", "pids"], "PASS")])
def test_older_rootless_runtime_requires_actual_cpu_delegation(store, monkeypatch, controllers, expected):
    info = {"host": {"security": {"rootless": True}, "cgroupVersion": "v2", "cgroupControllers": controllers},
            "version": {"Version": "3.4.4"}, "store": {"graphDriverName": "overlay"}}
    monkeypatch.setattr("agentlab.host.checked", lambda *a, **k:
                        CommandResult(returncode=0, output=json.dumps(info), duration=0))
    rows = verify_host(store, profile="sandbox")
    result = next(row for row in rows if row["name"] == "host.rootless_podman")
    assert result["status"] == expected
    if expected == "FAIL":
        assert result["code"] == "CGROUP_DELEGATION_MISSING"


def test_cleanup_uses_flags_supported_by_podman_3(store, job, monkeypatch):
    sandbox = Sandbox(store)
    results = iter([{"State": {"Running": True}}, None])
    monkeypatch.setattr(sandbox, "inspect", lambda *a: next(results))
    commands = []

    def older_podman(argv, **kwargs):
        commands.append(argv)
        if "--time" in argv:
            raise LabError("COMMAND_FAILED", "unknown flag: --time")
        return CommandResult(returncode=0, output="removed", duration=0)

    monkeypatch.setattr("agentlab.sandbox.checked", older_podman)
    assert sandbox.destroy(job["job_id"])["verified_absent"] is True
    assert len(commands) == 1 and "--force" in commands[0]


@pytest.mark.parametrize("code", ["NO_IP", "SSH_TIMEOUT", "BOOT_TIMEOUT"])
def test_transient_readiness_failure_gets_one_retry_with_shared_budget(store, monkeypatch, code):
    validation = Validation(store, "full", vm_timeout=240)
    clock = [0]
    budgets = []
    monkeypatch.setattr("agentlab.validate.time.monotonic", lambda: clock[0])

    def call(tool, **args):
        budgets.append(args["timeout"])
        if len(budgets) == 1:
            clock[0] += args["timeout"] + 8  # Diagnostic collection also consumes the budget.
            raise LabError(code, "guest still starting", saw_ip=code != "NO_IP")
        return {"ssh_ready": True, "cloud_init_ready": True}

    monkeypatch.setattr(validation, "call", call)
    result = validation.wait_vm("job", "vm")
    assert budgets == [120, 112]
    assert result["recovered_after_retry"] and result["readiness_attempts"][0]["code"] == code


@pytest.mark.parametrize("code", ["SSH_HOST_KEY_MISMATCH", "OWNERSHIP_MISMATCH", "VM_STOPPED"])
def test_permanent_readiness_errors_do_not_retry(store, monkeypatch, code):
    validation = Validation(store, "full")
    calls = []

    def fail(*a, **k):
        calls.append(k)
        raise LabError(code, "permanent failure")

    monkeypatch.setattr(validation, "call", fail)
    with pytest.raises(LabError) as error:
        validation.wait_vm("job", "vm")
    assert len(calls) == 1 and error.value.code == code


def test_repeated_readiness_failure_is_not_reported_as_recovered(store, monkeypatch):
    validation = Validation(store, "full")

    def fail(*a, **k):
        raise LabError("NO_IP", "no address")

    monkeypatch.setattr(validation, "call", fail)
    with pytest.raises(LabError) as error:
        validation.wait_vm("job", "vm")
    assert error.value.code == "NO_IP" and len(error.value.data["readiness_attempts"]) == 2


def test_host_key_mismatch_is_classified_without_waiting_for_timeout(store, vm_record, monkeypatch):
    broker = VMBroker(store)
    monkeypatch.setattr(broker, "status", lambda *a: {"state": "running", "ip": "192.168.122.2"})
    monkeypatch.setattr(broker, "ssh_args", lambda *a: ["ssh", "fixture"])
    monkeypatch.setattr("agentlab.vm.run", lambda *a, **k:
                        CommandResult(returncode=255, output="Host key verification failed", duration=0))
    with pytest.raises(LabError) as error:
        broker.wait(vm_record["job_id"], vm_record["vm_id"], timeout=240)
    assert error.value.code == "SSH_HOST_KEY_MISMATCH"
