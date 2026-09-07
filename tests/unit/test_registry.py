import json

import pytest

from agentlab.build import BuildBroker
from agentlab.models import LabError
from agentlab.store import new_id


@pytest.mark.parametrize("tool,args", [
    ("job_status", {"job_id": "../../etc"}),
    ("job_create", {"shell": "anything"}),
    ("vm_deploy", {"job_id": "JOB-" + "1" * 32, "image_id": "IMAGE-" + "a" * 64,
                   "profile": "unlimited"}),
    ("file_read", {"job_id": "JOB-" + "1" * 32, "path": 42}),
    ("git_show", {"job_id": "JOB-" + "1" * 32, "source_id": "SOURCE-" + "1" * 32,
                  "commit": "--output=/etc/x"}),
    ("vm_wait", {"job_id": "JOB-" + "1" * 32, "vm_id": "VM-" + "2" * 32, "timeout": "20"}),
])
def test_reject_malformed_before_broker(registry, tool, args):
    assert registry.call(tool, args).code == "INVALID_INPUT"


def test_unknown_ids_and_cross_job_source(registry, store, job):
    assert registry.call("job_status", {"job_id": new_id("JOB")}).code == "UNKNOWN_JOB"
    other = store.create_job()
    result = registry.call("git_status", {"job_id": job["job_id"], "source_id": other["current_source_id"]})
    assert result.code == "SOURCE_MISMATCH"


def test_errors_logged_outside_workspace(registry, store, job):
    registry.call("file_read", {"job_id": job["job_id"], "path": "../../etc/passwd"})
    events = [json.loads(line) for line in (store.job(job["job_id"]) / "events.jsonl").read_text().splitlines()]
    assert events[-1]["status"] == "FAIL"
    assert events[-1]["code"] == "PATH_DENIED"
    assert not (store.job(job["job_id"]) / "workspace/state.json").exists()


def test_patch_selection_invalidates_old_build(registry, store, job, passing_build):
    result = registry.call("patch_create", {"job_id": job["job_id"], "source_id": job["current_source_id"]})
    assert result.status == "PASS"
    state = store.state(job["job_id"])
    assert state.current_build_id is None and state.build_status == "NOT_RUN"
    assert (store.job(job["job_id"]) / "builds" / f"{passing_build['build_id']}.json").exists()
    with pytest.raises(LabError, match="No passing build"):
        BuildBroker(store).current(job["job_id"])


def test_source_mutation_and_artifact_mutation_invalidate(store, job, passing_build):
    source = store.source(job["job_id"], job["current_source_id"])
    (source / "unexpected.py").write_text("changed")
    with pytest.raises(LabError) as exc:
        BuildBroker(store).current(job["job_id"])
    assert exc.value.code == "STALE_BUILD"


def test_artifact_digest_enforced(store, job, passing_build):
    artifact = store.job(job["job_id"]) / "builds" / f"{passing_build['artifact_id']}.whl"
    artifact.write_bytes(b"tampered")
    with pytest.raises(LabError) as exc:
        BuildBroker(store).current(job["job_id"])
    assert exc.value.code == "ARTIFACT_MISMATCH"


def test_all_tool_schemas_forbid_extra_arguments(registry):
    for schemas in registry.schemas().values():
        assert schemas["input"]["additionalProperties"] is False
        assert schemas["output"]["additionalProperties"] is False
