import json

import pytest

from agentlab.models import LabError
from agentlab.vm import TestBroker, VMBroker


@pytest.mark.parametrize("fault", ["BUILD_TIMEOUT", "DISK_FULL", "DEPENDENCY_FAILURE"])
def test_build_faults_explicit_and_revoke_success(registry, store, job, passing_build, fault):
    registry.call("fault_set", {"job_id": job["job_id"], "fault": fault})
    result = registry.call("build_run", {"job_id": job["job_id"], "source_id": job["current_source_id"]})
    assert result.status == "FAIL" and result.code == fault and result.data["simulated"]
    assert store.state(job["job_id"]).current_build_id is None
    record = json.loads((store.job(job["job_id"]) / "builds" / f"{result.data['build_id']}.json").read_text())
    assert record["status"] == "FAIL" and record["simulated"] is True
    registry.call("fault_clear", {"job_id": job["job_id"]})
    assert store.state(job["job_id"]).fault is None


@pytest.mark.parametrize("fault", ["NO_IP", "SSH_TIMEOUT", "BOOT_TIMEOUT", "HYPERVISOR_UNAVAILABLE",
                                   "STALE_VM_ID", "WRONG_IMAGE_BINDING", "SSH_DISCONNECT"])
def test_vm_faults_do_not_touch_hypervisor(registry, store, job, vm_record, monkeypatch, fault):
    def forbidden(*args, **kwargs):
        pytest.fail("Injected fault must not invoke the hypervisor")
    monkeypatch.setattr("agentlab.vm.virsh", forbidden)
    registry.call("fault_set", {"job_id": job["job_id"], "fault": fault})
    result = registry.call("vm_wait", {"job_id": job["job_id"], "vm_id": vm_record["vm_id"]})
    assert result.status == "FAIL" and result.code == fault and result.data["simulated"]


def test_wrong_image_rejected_before_ssh(store, job, vm_record, monkeypatch):
    monkeypatch.setattr(VMBroker, "wait", lambda *a, **k: pytest.fail("Must not connect"))
    with pytest.raises(LabError) as exc:
        TestBroker(store).run(job["job_id"], vm_record["vm_id"], "IMAGE-" + "b" * 64, "smoke")
    assert exc.value.code == "WRONG_IMAGE_BINDING"


def test_cross_job_vm_rejected(store, vm_record):
    other = store.create_job()
    with pytest.raises(LabError) as exc:
        VMBroker(store).record(other["job_id"], vm_record["vm_id"])
    assert exc.value.code == "STALE_VM_ID"
