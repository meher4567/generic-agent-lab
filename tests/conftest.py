import os

import pytest

from agentlab.build import BuildBroker
from agentlab.policy import sha256, source_hash
from agentlab.registry import Registry
from agentlab.store import Store, atomic_json, new_id


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_VM_STORAGE", str(tmp_path / "vm-storage"))
    (tmp_path / "vm-storage").mkdir(mode=0o711)
    return Store(tmp_path / "runtime")


@pytest.fixture
def job(store):
    return store.create_job()


@pytest.fixture
def registry(store):
    return Registry(store)


@pytest.fixture
def passing_build(store, job):
    """Synthetic trusted state for unit tests; never counted as a real build proof."""
    job_id = job["job_id"]
    state = store.state(job_id)
    build_id, artifact_id = new_id("BUILD"), new_id("ARTIFACT")
    source = store.source(job_id, state.current_source_id)
    content = b"unit-test-only-artifact"
    record = {"job_id": job_id, "source_id": state.current_source_id, "build_id": build_id,
              "artifact_id": artifact_id, "artifact_sha256": sha256(content), "status": "PASS",
              "source_hash": source_hash(source), "patch_id": None}
    (store.job(job_id) / "builds" / f"{artifact_id}.whl").write_bytes(content)
    atomic_json(store.job(job_id) / "builds" / f"{build_id}.json", record)
    state.current_build_id, state.build_status = build_id, "PASS"
    store.save(state)
    assert BuildBroker(store).current(job_id) == record
    return record


@pytest.fixture
def vm_record(store, job):
    from agentlab.store import now
    vm_id = new_id("VM")
    record = {"vm_id": vm_id, "job_id": job["job_id"], "image_id": "IMAGE-" + "a" * 64,
              "instance": store.instance, "domain": "unit-test-domain", "uuid": "unit-test-uuid",
              "created_at": now(), "status": "RUNNING", "mac": "52:54:00:11:22:33"}
    folder = store.job(job["job_id"]) / "vm" / vm_id
    folder.mkdir()
    atomic_json(folder / "record.json", record)
    return record


def pytest_collection_modifyitems(config, items):
    for item in items:
        if "integration" in item.keywords and os.environ.get("LAB_RUN_INTEGRATION") != "1":
            item.add_marker(pytest.mark.skip(reason="Set LAB_RUN_INTEGRATION=1 for real Podman tests"))
        if "kvm" in item.keywords and os.environ.get("LAB_RUN_KVM") != "1":
            item.add_marker(pytest.mark.skip(reason="Set LAB_RUN_KVM=1 on the provisioned Ubuntu host"))
