"""Bounded, local-only diagnostic collection; also runs without installed dependencies."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import stat
import subprocess
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

LIMIT = 64 * 1024
RUN_ID = re.compile(r"RUN-[0-9a-f]{32}")
JOB_ID = re.compile(r"JOB-[0-9a-f]{32}")


def redact(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?(?:-----END [^-]*PRIVATE KEY-----|\Z)",
                  "[REDACTED PRIVATE KEY]", text, flags=re.S)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b",
                  "[REDACTED TOKEN]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1[REDACTED]", text)
    text = re.sub(r'''(?i)((?:password|passwd|token|secret|api[_-]?key|authorization|credential)[\w-]*["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)''',
                  r'\1"[REDACTED]"', text)
    # Strip terminal escape sequences from generated human-readable diagnostics.
    return re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)


def scrub(value):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if re.search(r"(?i)password|secret|token|credential|private.key|authorization", k)
                else scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def read_tail(root: Path, relative: str, limit: int = LIMIT) -> str:
    """No links/special files; read only the bounded tail of an allowlisted path."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(p in (".", "..") for p in parts):
        raise ValueError("Invalid diagnostic path")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        target = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(target)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Diagnostic source must be a regular file with one link")
            os.lseek(target, max(0, info.st_size - limit), os.SEEK_SET)
            data = os.read(target, limit).decode(errors="replace")
            return ("[earlier content omitted]\n" if info.st_size > limit else "") + data
        finally:
            os.close(target)
    finally:
        os.close(fd)


def collect(runtime: Path, output_dir: Path | None = None, *, source: Path | None = None) -> dict:
    files: dict[str, str] = {}
    omissions: list[str] = []

    def include(root: Path, relative: str, name: str, limit: int = LIMIT):
        try:
            data = read_tail(root, relative, limit)
            try:
                files[name] = json.dumps(scrub(json.loads(data)), indent=2) if name.endswith(".json") else redact(data)
            except ValueError:
                files[name] = redact(data)
            return files[name]
        except (OSError, ValueError) as exc:
            omissions.append(f"{name}: {type(exc).__name__}: {exc}")
            return None

    pointer = include(runtime, "reports/latest.json", "latest.json")
    report = {}
    try:
        run_id = json.loads(pointer or "{}").get("run_id", "")
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("No valid latest run ID")
        # A whole JSON report is useful for automated inspection; bounded to 2 MiB.
        raw = include(runtime, f"reports/{run_id}/report.json", "report.json", 2 * 1024**2)
        report = json.loads(raw or "{}")
        for name in ("summary.md", "software-tests.log", "sandbox-image.log"):
            include(runtime, f"reports/{run_id}/{name}", f"run/{name}")
        for check in report.get("checks", [])[:300]:
            name = check.get("log", "")
            if re.fullmatch(r"failure-[0-9]+\.log", name):
                include(runtime, f"reports/{run_id}/{name}", f"run/{name}")
        for job in report.get("jobs", [])[:8]:
            if not isinstance(job, str) or not JOB_ID.fullmatch(job):
                continue
            include(runtime, f"jobs/{job}/events.jsonl", f"jobs/{job}/events.jsonl")
            logs = runtime / "jobs" / job / "logs"
            for log in sorted(logs.glob("*.log"))[-12:]:
                include(runtime, f"jobs/{job}/logs/{log.name}", f"jobs/{job}/{log.name}")
    except (ValueError, TypeError, AttributeError, OSError) as exc:
        omissions.append(f"Latest report unavailable or corrupt: {exc}")

    setup_roots = [(Path("/var/log/generic-agent-lab"), "bootstrap")]
    if source:
        setup_roots.append((source / "reports/setup", "python-setup"))
    for root, kind in setup_roots:
        raw = include(root, f"{kind}.json", f"setup/{kind}.json")
        try:
            name = json.loads(raw or "{}").get("log_file", "")
            if re.fullmatch(r"(?:bootstrap|python-setup)-[0-9TZ-]+\.log", name):
                include(root, name, f"setup/{name}")
        except (ValueError, TypeError, AttributeError) as exc:
            omissions.append(f"{kind} status could not be parsed: {exc}")

    commands = {
        "disk": ["df", "-h", str(runtime if runtime.exists() else Path("/tmp"))],
        "inodes": ["df", "-i", str(runtime if runtime.exists() else Path("/tmp"))],
        "memory": ["free", "-h"],
        "identity": ["id"],
        "libvirt-version": ["virsh", "--connect", "qemu:///system", "version"],
        "services": ["systemctl", "status", "libvirtd.service", "generic-agent-lab-reaper.timer",
                     "--no-pager", "--lines=10"],
    }
    if os.geteuid() != 0:
        commands["podman"] = ["podman", "info", "--format", "json"]
    for name, argv in commands.items():
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=8, env={**{k: v for k, v in os.environ.items() if k in {
                                        "PATH", "HOME", "USER", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME",
                                        "XDG_DATA_HOME", "DBUS_SESSION_BUS_ADDRESS"}}, "LC_ALL": "C"})
            output = (result.stdout + result.stderr)[-LIMIT:].decode(errors="replace")
            files[f"host/{name}.txt"] = f"Exit: {result.returncode}\n" + redact(output)
        except (OSError, subprocess.TimeoutExpired) as exc:
            omissions.append(f"host/{name}: {type(exc).__name__}: {exc}")
    include(Path("/etc"), "os-release", "host/os-release.txt")
    files["manifest.json"] = json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": str(runtime), "collection_status": "PARTIAL" if omissions else "COMPLETE",
        "omissions": [redact(item) for item in omissions],
        "redaction": "Best effort; review before sharing. No upload is performed.",
        "excluded": ["private keys", "cloud-init user-data", "environment dump", "source workspaces",
                     "VM disks", "container inspect environment"]}, indent=2)
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="generic-agent-lab-diagnostics-"))
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    archive = output_dir / f"diagnostics-{uuid.uuid4().hex}.tar.gz"
    fd = os.open(archive, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as output:
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            for name, value in files.items():
                data = value.encode()
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                bundle.addfile(info, io.BytesIO(data))
    return {"bundle": str(archive), "files": len(files), "omissions": omissions,
            "note": "Stored locally; review redacted contents before sharing. Nothing was uploaded."}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(collect(args.runtime, args.output_dir, source=args.source), indent=2))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Diagnostic collection failed: {exc}. Try --output-dir on a writable filesystem.\n")
