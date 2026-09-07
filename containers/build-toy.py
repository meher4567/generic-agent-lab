"""Trusted profile; project code runs only inside the bounded offline container."""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

build_id = sys.argv[1]
assert re.fullmatch(r"BUILD-[0-9a-f]{32}", build_id)
output = Path("/job/output") / build_id
output.mkdir()
with tempfile.TemporaryDirectory(prefix="toy-build-") as temp:
    source = Path(temp) / "source"
    shutil.copytree("/job/source", source, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    env = dict(os.environ, PYTHONPATH=str(source / "src"))
    for command in [
        ["python3", "-m", "compileall", "-q", "src"],
        ["python3", "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        ["python3", "-m", "build", "--wheel", "--no-isolation", "--outdir", str(output)],
    ]:
        print("RUN", " ".join(command), flush=True)
        result = subprocess.run(command, cwd=source, env=env, check=False)
        if result.returncode:
            sys.exit(result.returncode)
