"""Bounded subprocesses: argv only, fixed environment, timeout and output ceiling."""
from __future__ import annotations

import os
import selectors
import shlex
import signal
import subprocess
import time

from .diagnostics import redact
from .models import CommandResult, LabError


def run(argv: list[str], *, timeout: float = 30, input_text: str | None = None,
        limit: int = 1024 * 1024, separate_stderr: bool = False) -> CommandResult:
    if input_text is not None and len(input_text.encode()) > 4096:
        raise LabError("INPUT_LIMIT", "Subprocess script input exceeds 4096 bytes")
    env = {k: v for k, v in os.environ.items() if k in {
        "PATH", "HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
        "XDG_CONFIG_HOME", "XDG_DATA_HOME", "SSL_CERT_FILE", "SSL_CERT_DIR",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    }}
    env.update(LC_ALL="C", LANG="C", GIT_TERMINAL_PROMPT="0")
    start = time.monotonic()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE if input_text is not None else
                                subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT,
                                env=env, start_new_session=True)
    except FileNotFoundError as exc:
        raise LabError("MISSING_DEPENDENCY", f"Required executable not found: {argv[0]}") from exc
    # Inputs here are bounded scripts/patches, below PIPE_BUF, not artifact streams.
    if input_text is not None:
        try:
            proc.stdin.write(input_text.encode())
            proc.stdin.close()
        except BrokenPipeError:
            pass
    out = bytearray()
    errors = bytearray()
    timed_out = limited = False
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(proc.stdout, selectors.EVENT_READ, out)
            if separate_stderr:
                selector.register(proc.stderr, selectors.EVENT_READ, errors)
            while selector.get_map():
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    timed_out = True
                    break
                for key, _ in selector.select(min(remaining, 0.2)):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    key.data.extend(chunk[:max(0, limit - len(out) - len(errors))])
                    if len(out) + len(errors) >= limit:
                        limited = True
                        break
                if limited:
                    break
            if timed_out or limited:
                kill_group(proc)
            try:
                proc.wait(timeout=max(0.1, timeout - (time.monotonic() - start)))
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_group(proc)
                proc.wait()
    finally:
        if proc.poll() is None:
            kill_group(proc)
            proc.wait()
        proc.stdout.close()
        if separate_stderr:
            proc.stderr.close()
    return CommandResult(returncode=proc.returncode, output=out.decode(errors="replace"),
                         stderr=errors.decode(errors="replace"),
                         duration=round(time.monotonic() - start, 3), timed_out=timed_out,
                         output_limited=limited)


def kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def checked(argv: list[str], **kwargs) -> CommandResult:
    result = run(argv, **kwargs)
    context = {**result.model_dump(), "command": redact(shlex.join(argv))[:4000],
               "timeout_seconds": kwargs.get("timeout", 30)}
    if result.timed_out:
        raise LabError("COMMAND_TIMEOUT", f"{argv[0]} exceeded its deadline", **context)
    if result.output_limited:
        raise LabError("OUTPUT_LIMIT", f"{argv[0]} exceeded its output limit", **context)
    if result.returncode:
        raise LabError("COMMAND_FAILED", f"{argv[0]} failed with exit code {result.returncode}", **context)
    return result
