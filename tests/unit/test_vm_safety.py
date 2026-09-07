
import pytest

from agentlab.models import CommandResult, LabError
from agentlab.store import atomic_json
from agentlab.vm import VMBroker


def command(output):
    return CommandResult(returncode=0, output=output, duration=0.0)


def test_domain_prefix_alone_never_authorizes_cleanup(store, vm_record, monkeypatch):
    invoked = []

    def fake(*args, **kwargs):
        invoked.append(args)
        if args[0] == "list":
            return command(vm_record["domain"])
        if args[0] == "dumpxml":
            return command("<domain><uuid>unowned</uuid></domain>")
        pytest.fail("Must not destroy an unowned domain")
    monkeypatch.setattr("agentlab.vm.virsh", fake)
    with pytest.raises(LabError) as exc:
        VMBroker(store).destroy(vm_record["job_id"], vm_record["vm_id"])
    assert exc.value.code == "OWNERSHIP_MISMATCH"
    assert len(invoked) == 2


def test_unavailable_hypervisor_does_not_delete_disk(store, vm_record, monkeypatch):
    broker = VMBroker(store)
    disk = broker.storage / vm_record["vm_id"]
    disk.mkdir()
    (disk / "overlay.qcow2").write_bytes(b"retain for retry")

    def unavailable(*args, **kwargs):
        raise LabError("HYPERVISOR_UNAVAILABLE", "offline")
    monkeypatch.setattr("agentlab.vm.virsh", unavailable)
    with pytest.raises(LabError):
        broker.destroy(vm_record["job_id"], vm_record["vm_id"])
    assert (disk / "overlay.qcow2").exists()


def test_absent_owned_domain_cleanup_preserves_base(store, vm_record, monkeypatch):
    broker = VMBroker(store)
    base = broker.storage / "base-unit.qcow2"
    base.write_bytes(b"base")
    disk = broker.storage / vm_record["vm_id"]
    disk.mkdir()
    (disk / "overlay.qcow2").write_bytes(b"overlay")
    folder = store.job(vm_record["job_id"]) / "vm" / vm_record["vm_id"]
    (folder / "client_key").write_text("private material fixture")
    monkeypatch.setattr("agentlab.vm.virsh", lambda *a, **k: command(""))
    assert broker.destroy(vm_record["job_id"], vm_record["vm_id"])["verified_absent"]
    assert base.read_bytes() == b"base" and not disk.exists()
    assert not (folder / "client_key").exists()
    assert broker.destroy(vm_record["job_id"], vm_record["vm_id"])["verified_absent"]


def test_ssh_pins_per_vm_host_key_and_ignores_user_config(store, vm_record):
    folder = store.job(vm_record["job_id"]) / "vm" / vm_record["vm_id"]
    (folder / "host_key.pub").write_text("ssh-ed25519 AAAATEST fixture")
    args = VMBroker(store).ssh_args(vm_record, "192.168.122.2")
    assert "StrictHostKeyChecking=yes" in args
    assert args[1:3] == ["-F", "/dev/null"]
    assert f"HostKeyAlias={vm_record['vm_id']}" in args
    assert "StrictHostKeyChecking=no" not in args


def test_image_registration_is_job_and_artifact_bound(store, job, passing_build):
    broker = VMBroker(store)
    atomic_json(broker.storage / "base.json", {"sha256": "a" * 64})
    record = broker.image_register(job["job_id"])
    assert record["artifact_sha256"] == passing_build["artifact_sha256"]
    assert record["job_id"] == job["job_id"]
    assert broker.image(job["job_id"], record["image_id"]) == record


def test_vm_quota_is_enforced_before_disk_creation(store, job, passing_build, monkeypatch):
    broker = VMBroker(store)
    atomic_json(broker.storage / "base.json", {"sha256": "a" * 64})
    image = broker.image_register(job["job_id"])
    monkeypatch.setattr("agentlab.vm.virsh", lambda *a, **k: command(""))
    monkeypatch.setattr(broker, "records", lambda: [{"status": "RUNNING"}] * 3)
    with pytest.raises(LabError) as exc:
        broker.deploy(job["job_id"], image["image_id"], "small")
    assert exc.value.code == "VM_QUOTA"
    assert not list(broker.storage.glob("VM-*"))


def test_expired_vm_is_cleaned_before_guest_access(store, vm_record, monkeypatch):
    import xml.etree.ElementTree as ET
    broker = VMBroker(store)
    vm_record["created_at"] = "2020-01-01T00:00:00+00:00"
    broker.save_record(vm_record)
    removed = []
    monkeypatch.setattr(broker, "domain_xml", lambda record: ET.fromstring("<domain/>"))
    monkeypatch.setattr(broker, "destroy", lambda job, vm: removed.append(vm))
    with pytest.raises(LabError) as exc:
        broker.status(vm_record["job_id"], vm_record["vm_id"])
    assert exc.value.code == "VM_EXPIRED" and removed == [vm_record["vm_id"]]
