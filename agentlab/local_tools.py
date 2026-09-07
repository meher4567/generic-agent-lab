from __future__ import annotations

from .models import LabError
from .policy import list_files, read_file, sha256, source_hash
from .sandbox import Sandbox
from .store import Store, atomic_json, new_id


class LocalTools:
    def __init__(self, store: Store):
        self.store, self.sandbox = store, Sandbox(store)

    def file_read(self, job_id: str, path: str) -> dict:
        content = read_file(self.store.job(job_id) / "workspace", path, limit=256 * 1024)
        return {"path": path, "content": content.decode(errors="replace"), "sha256": sha256(content)}

    def file_list(self, job_id: str, path: str) -> dict:
        return {"files": list_files(self.store.job(job_id) / "workspace", path)}

    def file_find(self, job_id: str, query: str) -> dict:
        root = self.store.job(job_id) / "workspace"
        matches = []
        for name in list_files(root):
            for number, line in enumerate(read_file(root, name).decode(errors="replace").splitlines(), 1):
                if query in line:
                    matches.append({"path": name, "line": number, "text": line[:512]})
                if len(matches) >= 200:
                    return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def git(self, job_id: str, source_id: str, args: list[str]) -> dict:
        self.store.source(job_id, source_id)
        result = self.sandbox.execute(job_id, ["git", "-c", "core.hooksPath=/dev/null",
            "-c", "core.fsmonitor=false", "-c", "core.pager=cat", "-c", "diff.external=",
            "-c", "core.attributesFile=/dev/null", *args], workdir="/job/source")
        if result.returncode:
            raise LabError("GIT_FAILED", "Sandbox Git operation failed", **result.model_dump())
        return result.model_dump()

    def git_init(self, job_id: str, source_id: str) -> dict:
        self.git(job_id, source_id, ["init", "-b", "main"])
        current = self.sandbox.execute(job_id, ["git", "rev-parse", "--verify", "HEAD"],
                                       workdir="/job/source")
        if current.returncode:
            self.git(job_id, source_id, ["add", "--", "."])
            self.git(job_id, source_id, ["-c", "user.name=Lab Fixture", "-c",
                "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-m", "Add generic toy fixture"])
        return self.git(job_id, source_id, ["rev-parse", "HEAD"])

    def git_status(self, job_id: str, source_id: str) -> dict:
        return self.git(job_id, source_id, ["status", "--porcelain=v1"])

    def git_show(self, job_id: str, source_id: str, commit: str) -> dict:
        return self.git(job_id, source_id, ["show", "--no-ext-diff", "--no-textconv", commit, "--"])

    def git_log(self, job_id: str, source_id: str, query: str | None) -> dict:
        args = ["log", "-n", "20", "--oneline", "--no-decorate"]
        if query:
            args += ["--fixed-strings", f"--grep={query}"]
        return self.git(job_id, source_id, args + ["--"])

    def git_diff(self, job_id: str, source_id: str, a: str, b: str | None) -> dict:
        return self.git(job_id, source_id, ["diff", "--no-ext-diff", "--no-textconv", a,
                        *([b] if b else []), "--"])

    def patch_create(self, job_id: str, source_id: str, variant: str) -> dict:
        self.store.source(job_id, source_id)
        old, new = {
            "fix": ("    return a - b", "    return a + b"),
            "next": ("    return a + b", "    return int(a + b)"),
            "invalid": ("    return missing", "    return a + b"),
        }[variant]
        patch = ("diff --git a/src/toy_lab/__init__.py b/src/toy_lab/__init__.py\n"
                 "--- a/src/toy_lab/__init__.py\n+++ b/src/toy_lab/__init__.py\n"
                 "@@ -1,2 +1,2 @@\n def add(a: int, b: int) -> int:\n"
                 f"-{old}\n+{new}\n")
        patch_id = new_id("PATCH")
        folder = self.store.job(job_id) / "patches"
        (folder / f"{patch_id}.patch").write_text(patch)
        record = {"patch_id": patch_id, "source_id": source_id, "sha256": sha256(patch.encode())}
        atomic_json(folder / f"{patch_id}.json", record)
        # Selecting even an unapplied new patch must revoke earlier success.
        self.store.invalidate(job_id, patch_id=patch_id)
        return record

    def patch(self, job_id: str, source_id: str, patch_id: str, *, apply: bool) -> dict:
        import json
        self.store.source(job_id, source_id)
        folder = self.store.job(job_id) / "patches"
        if not (folder / f"{patch_id}.json").exists():
            raise LabError("UNKNOWN_PATCH", "Patch is not registered in this job")
        record = json.loads((folder / f"{patch_id}.json").read_text())
        content = read_file(folder, f"{patch_id}.patch", limit=4096)
        if record["source_id"] != source_id or record["sha256"] != sha256(content):
            raise LabError("PATCH_MISMATCH", "Patch ownership or digest mismatch")
        # Patch bytes go to git stdin; privileged host tools never apply patches.
        result = self.sandbox.execute(job_id, ["git", "apply", *([] if apply else ["--check"]),
                    "--whitespace=error", "-"], input_text=content.decode(), workdir="/job/source")
        if result.returncode:
            raise LabError("PATCH_REJECTED", "Patch did not apply cleanly", **result.model_dump())
        if apply:
            self.store.invalidate(job_id, patch_id=patch_id)
            record["applied_source_hash"] = source_hash(self.store.source(job_id, source_id))
            atomic_json(folder / f"{patch_id}.json", record)
        return {**record, "applied": apply, "output": result.output}

    def patch_check(self, **kwargs) -> dict:
        return self.patch(**kwargs, apply=False)

    def patch_apply(self, **kwargs) -> dict:
        return self.patch(**kwargs, apply=True)
