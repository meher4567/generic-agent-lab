from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import JobState, LabError


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def atomic_json(path: Path, value) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x") as handle:
            os.chmod(temp, 0o600)
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path | None = None):
        self.root = (root or Path(os.environ.get("LAB_ROOT", Path.home() /
                      ".local/share/generic-agent-lab"))).absolute()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.root.is_symlink() or self.root.stat().st_uid != os.getuid():
            raise LabError("ROOT_DENIED", "Runtime must be owned by the lab user and not a symlink")
        os.chmod(self.root, 0o700)
        for folder in ("jobs", "cache", "reports"):
            (self.root / folder).mkdir(exist_ok=True, mode=0o700)
        with self.lock("installation"):
            marker = self.root / "installation.json"
            if not marker.exists():
                atomic_json(marker, {"instance": uuid.uuid4().hex})
            self.instance = json.loads(marker.read_text())["instance"]

    @contextmanager
    def lock(self, name: str):
        if not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise LabError("INVALID_ID", "Invalid lock identifier")
        with (self.root / f".{name}.lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def job(self, job_id: str) -> Path:
        if not re.fullmatch(r"JOB-[0-9a-f]{32}", job_id):
            raise LabError("INVALID_ID", "Malformed job ID")
        path = self.root / "jobs" / job_id
        if not path.is_dir() or path.is_symlink():
            raise LabError("UNKNOWN_JOB", "Job does not exist")
        return path

    def state(self, job_id: str) -> JobState:
        return JobState.model_validate_json((self.job(job_id) / "state.json").read_text())

    def save(self, state: JobState) -> None:
        atomic_json(self.job(state.job_id) / "state.json", state.model_dump())

    def event(self, job_id: str, event: str, **data) -> None:
        record = {"type": event, "job_id": job_id, "time": now(), **data}
        with (self.job(job_id) / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def create_job(self) -> dict:
        job_id, source_id = new_id("JOB"), new_id("SOURCE")
        job = self.root / "jobs" / job_id
        job.mkdir(mode=0o700)
        for folder in ("workspace", "reference", "patches", "logs", "builds", "images", "vm", "tests"):
            (job / folder).mkdir(mode=0o700)
        (job / "workspace/output").mkdir()
        fixture = Path(__file__).parent / "fixtures/toy-python-app"
        shutil.copytree(fixture, job / "workspace/source",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info"))
        (job / "reference/reference.txt").write_text("DO NOT MODIFY\n")
        (job / "reference/reference.txt").chmod(0o444)
        (job / "reference").chmod(0o555)
        state = JobState(job_id=job_id, status="CREATED", current_source_id=source_id,
                         created_at=now())
        self.save(state)
        self.event(job_id, "JOB_CREATED", source_id=source_id)
        return state.model_dump()

    def source(self, job_id: str, source_id: str) -> Path:
        if self.state(job_id).current_source_id != source_id:
            raise LabError("SOURCE_MISMATCH", "Source does not belong to this job")
        return self.job(job_id) / "workspace/source"

    def invalidate(self, job_id: str, *, patch_id: str | None = None) -> None:
        state = self.state(job_id)
        state.current_patch_id = patch_id or state.current_patch_id
        state.current_build_id = state.current_image_id = None
        state.current_source_hash = None
        state.build_status, state.status = "NOT_RUN", "READY"
        self.save(state)

    def jobs(self) -> list[str]:
        return sorted(p.name for p in (self.root / "jobs").iterdir()
                      if re.fullmatch(r"JOB-[0-9a-f]{32}", p.name) and not p.is_symlink())
