from __future__ import annotations

import json

from .models import LabError
from .policy import read_file, sha256, source_hash
from .sandbox import Sandbox
from .store import Store, atomic_json, new_id, now

BUILD_FAULTS = {"BUILD_TIMEOUT", "DISK_FULL", "DEPENDENCY_FAILURE"}


class BuildBroker:
    def __init__(self, store: Store):
        self.store = store

    def run(self, job_id: str, source_id: str, profile: str) -> dict:
        if profile != "python-toy":
            raise LabError("PROFILE_DENIED", "Unknown build profile")
        source = self.store.source(job_id, source_id)
        state = self.store.state(job_id)
        build_id = new_id("BUILD")
        record = {"job_id": job_id, "source_id": source_id, "build_id": build_id,
                  "patch_id": state.current_patch_id, "profile": profile, "started_at": now(),
                  "status": "RUNNING", "simulated": state.fault in BUILD_FAULTS}
        job = self.store.job(job_id)
        state.status, state.build_status = "RUNNING", "RUNNING"
        state.current_build_id = state.current_image_id = None
        self.store.save(state)
        try:
            before = source_hash(source)
            record["source_hash"] = before
            if state.fault in BUILD_FAULTS:
                raise LabError(state.fault, "Controlled build fault; no host damage", simulated=True)
            if state.current_patch_id:
                patch = json.loads((job / "patches" / f"{state.current_patch_id}.json").read_text())
                if patch.get("applied_source_hash") != before:
                    raise LabError("PATCH_NOT_APPLIED", "Apply the selected patch before building")
            result = Sandbox(self.store).execute(job_id,
                       ["python3", "/opt/lab/build-toy.py", build_id], timeout=120)
            record.update(duration=result.duration, log_id=f"{build_id}.log")
            (job / "logs" / f"{build_id}.log").write_text(result.output)
            if result.returncode:
                raise LabError("BUILD_FAILED", "Toy compile/test/package sequence failed",
                               returncode=result.returncode, log_id=record["log_id"])
            if source_hash(source) != before:
                raise LabError("SOURCE_CHANGED", "Source changed during the build")
            artifact = read_file(job / "workspace", f"output/{build_id}/toy_lab_app-0.1.0-py3-none-any.whl")
            artifact_id = new_id("ARTIFACT")
            destination = job / "builds" / f"{artifact_id}.whl"
            destination.write_bytes(artifact)
            destination.chmod(0o400)
            record.update(status="PASS", artifact_id=artifact_id, artifact_sha256=sha256(artifact),
                          artifact_size=len(artifact))
            state.current_build_id, state.current_source_hash = build_id, before
            state.build_status, state.status = "PASS", "READY"
            return record
        except LabError as exc:
            record.update(status="FAIL", code=exc.code, message=exc.message)
            state.build_status, state.status = exc.code, "FAILED"
            exc.data.update(build_id=build_id)
            raise
        finally:
            atomic_json(job / "builds" / f"{build_id}.json", record)
            self.store.save(state)

    def current(self, job_id: str) -> dict:
        state = self.store.state(job_id)
        if not state.current_build_id or state.build_status != "PASS":
            raise LabError("STALE_BUILD", "No passing build for the current source and patch")
        record = json.loads((self.store.job(job_id) / "builds" /
                             f"{state.current_build_id}.json").read_text())
        if (record["source_hash"] != source_hash(self.store.source(job_id, state.current_source_id))
                or record["patch_id"] != state.current_patch_id):
            self.store.invalidate(job_id)
            raise LabError("STALE_BUILD", "Source or patch changed after this build")
        content = read_file(self.store.job(job_id) / "builds", f"{record['artifact_id']}.whl")
        if sha256(content) != record["artifact_sha256"]:
            raise LabError("ARTIFACT_MISMATCH", "Build artifact hash no longer matches")
        return record
