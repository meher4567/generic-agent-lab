from __future__ import annotations

import json
import os

from .models import LabError
from .process import checked, run
from .store import Store, atomic_json

SANDBOX_IMAGE = "localhost/generic-agent-lab-sandbox:0.1.0"


class Sandbox:
    def __init__(self, store: Store):
        self.store = store

    def name(self, job_id: str) -> str:
        self.store.job(job_id)
        return f"gal-{self.store.instance[:8]}-{job_id[4:]}"

    def inspect(self, job_id: str) -> dict | None:
        name = self.name(job_id)
        exists = run(["podman", "container", "exists", name])
        if exists.returncode == 1 and not exists.timed_out:
            return None
        if exists.returncode or exists.timed_out:
            raise LabError("PODMAN_UNAVAILABLE", "Could not query container ownership",
                           **exists.model_dump())
        data = json.loads(checked(["podman", "inspect", name], separate_stderr=True).output)[0]
        labels = data.get("Config", {}).get("Labels", {})
        if (labels.get("lab.instance") != self.store.instance or
                labels.get("lab.job") != job_id):
            raise LabError("OWNERSHIP_MISMATCH", "Refusing to operate on an unowned container")
        return data

    def create(self, job_id: str) -> dict:
        if os.geteuid() == 0:
            raise LabError("ROOT_DENIED", "Run the control plane as the dedicated non-root user")
        info = json.loads(checked(["podman", "info", "--format", "json"], separate_stderr=True).output)
        if not info["host"]["security"]["rootless"]:
            raise LabError("ROOTLESS_REQUIRED", "Rootless Podman is required")
        existing = self.inspect(job_id)
        if existing:
            if existing["State"]["Running"]:
                return {"container_id": existing["Id"], "reused": True}
            raise LabError("SANDBOX_STOPPED", "Destroy the stopped job sandbox before recreating it")
        job = self.store.job(job_id)
        result = checked([
            "podman", "run", "-d", "--pull=never", "--name", self.name(job_id),
            "--label", f"lab.instance={self.store.instance}", "--label", f"lab.job={job_id}",
            "--userns=keep-id", "--user", f"{os.getuid()}:{os.getgid()}",
            "--read-only", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=512m",
            "--memory-swap=512m", "--cpus=1", "--cgroupns=private",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m,mode=1777",
            "--volume", f"{job / 'workspace'}:/job:rw,Z",
            "--volume", f"{job / 'reference'}:/reference:ro,Z",
            SANDBOX_IMAGE,
        ], timeout=120, separate_stderr=True)
        record = {"container_id": result.output.strip(), "image": SANDBOX_IMAGE}
        atomic_json(job / "sandbox.json", record)
        state = self.store.state(job_id)
        state.status = "READY"
        self.store.save(state)
        return record

    def execute(self, job_id: str, argv: list[str], *, timeout: int = 30,
                input_text: str | None = None, workdir: str = "/job"):
        info = self.inspect(job_id)
        if not info or not info["State"]["Running"]:
            raise LabError("SANDBOX_NOT_RUNNING", "Create this job's sandbox first")
        command = ["podman", "exec"]
        if input_text is not None:
            command.append("-i")
        command += ["--workdir", workdir, self.name(job_id), "timeout", "--signal=TERM",
                    "--kill-after=3", str(timeout), *argv]
        result = run(command, timeout=timeout + 10, input_text=input_text)
        if result.timed_out or result.output_limited or result.returncode in (124, 137):
            # Killing the client alone would leave a container exec running.
            cleanup_error = None
            try:
                checked(["podman", "stop", "--time", "1", self.name(job_id)], timeout=20)
            except (LabError, OSError) as exc:
                cleanup_error = str(exc)
            raise LabError("BUILD_TIMEOUT" if result.returncode in (124, 137) or result.timed_out
                           else "OUTPUT_LIMIT", "Sandbox stop failed after execution limit; cleanup required"
                           if cleanup_error else "Sandbox stopped after execution limit",
                           cleanup_error=cleanup_error, **result.model_dump())
        return result

    def destroy(self, job_id: str) -> dict:
        info = self.inspect(job_id)
        if info:
            # Podman 3.x (Ubuntu 22.04) does not support `rm --time`.
            checked(["podman", "rm", "--force", self.name(job_id)], timeout=30)
        if self.inspect(job_id) is not None:
            raise LabError("CLEANUP_FAILED", "Job sandbox still exists")
        return {"removed": bool(info), "verified_absent": True}
