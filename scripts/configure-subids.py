#!/usr/bin/env python3
"""Allocate missing subordinate ranges without overwriting existing allocations."""
import os
import subprocess
import sys
from pathlib import Path

if os.geteuid() != 0 or sys.argv[1:] != ["agentlab"]:
    raise SystemExit("Run from bootstrap as root for the dedicated agentlab account")
for kind, option in (("subuid", "--add-subuids"), ("subgid", "--add-subgids")):
    path = Path("/etc") / kind
    rows = [line.split(":") for line in path.read_text().splitlines() if line.strip()]
    parsed = [(name, int(start), int(count)) for name, start, count in rows]
    if any(name == "agentlab" and count >= 65536 for name, start, count in parsed):
        continue
    start = max([100000] + [begin + count for name, begin, count in parsed])
    if start + 65536 > 2**32 - 1:
        raise SystemExit(f"No available {kind} range")
    subprocess.run(["usermod", option, f"{start}-{start + 65535}", "agentlab"], check=True)
