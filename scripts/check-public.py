#!/usr/bin/env python3
"""Check staged blobs, never print matching secret contents."""
import argparse
import re
import subprocess
from pathlib import PurePosixPath

parser = argparse.ArgumentParser()
parser.add_argument("--deny-term", action="append", default=[], help="Additional private names to reject")
args = parser.parse_args()
paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
patterns = [
    re.compile(rb"-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----"),
    re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
    re.compile(rb"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(rb"(?i)(?:api_key|secret_key|access_token)\s*[=:]\s*['\"][A-Za-z0-9_\-]{24,}['\"]"),
]
forbidden_dirs = {".venv", "runtime", "reports", "vm-images", "artifacts", "__pycache__", ".local"}
problems = []
checked_count = 0
for name in filter(None, paths):
    path = PurePosixPath(name)
    if (forbidden_dirs.intersection(path.parts) or path.name.startswith(".env") or
            path.suffix in {".qcow2", ".img", ".iso", ".log", ".pem", ".key"} or
            (path.name.startswith("diagnostics-") and path.name.endswith(".tar.gz")) or
            path.name in {"id_ed25519", "id_rsa", "Generic_Agent_Lab_Infrastructure_Setup_and_Tool_Validation_Guide.md"}):
        problems.append((name, "forbidden runtime/private file"))
        continue
    content = subprocess.check_output(["git", "show", f":{name}"])
    checked_count += 1
    if len(content) > 2 * 1024 * 1024:
        problems.append((name, "unexpectedly large public file"))
    if any(pattern.search(content) for pattern in patterns):
        problems.append((name, "possible credential"))
    if any(term.casefold() in content.decode(errors="replace").casefold() for term in args.deny_term):
        problems.append((name, "private deny-term matched"))
for name, reason in problems:
    print(f"[FAIL] {name}: {reason}")
if problems:
    raise SystemExit(1)
print(f"[PASS] Reviewed {checked_count} staged files: no forbidden paths or credential patterns found.")
