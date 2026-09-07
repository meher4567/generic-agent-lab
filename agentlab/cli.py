from __future__ import annotations

import json
import os
import tempfile
import traceback
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from typer.core import TyperGroup

from .diagnostics import RUN_ID, collect, redact
from .host import verify_host
from .models import LabError
from .recovery import error_code, recovery
from .registry import Registry
from .store import Store
from .validate import Validation
from .vm import VMBroker


class DiagnosticGroup(TyperGroup):
    def invoke(self, ctx):
        try:
            return super().invoke(ctx)
        except (typer.Exit, typer.Abort, typer.BadParameter):
            raise
        except (LabError, OSError, ValueError, KeyError, RuntimeError, TypeError, AssertionError) as exc:
            code = error_code(exc)
            value = {"status": "FAIL", "code": code, "message": redact(str(exc)),
                     **recovery(code, str(exc))}
            try:
                # Independent of Store: still works when runtime setup or state loading fails.
                folder = Path(tempfile.mkdtemp(prefix="generic-agent-lab-error-"))
                log = folder / "error.log"
                log.write_text(redact(traceback.format_exc()))
                log.chmod(0o600)
                value["log"] = str(log)
            except OSError as log_error:
                value["logging_error"] = str(log_error)
            emit(value)
            raise typer.Exit(1) from exc


app = typer.Typer(cls=DiagnosticGroup, no_args_is_help=True,
                  help="Set up and prove a generic engineering infrastructure lab.")


class Profile(str, Enum):
    full = "full"
    sandbox = "sandbox"
    software = "software"


def emit(value):
    typer.echo(json.dumps(value, indent=2))


def invoke(name: str, **arguments):
    try:
        result = Registry(Store()).call(name, arguments)
        emit(result.model_dump())
        if result.status == "FAIL":
            raise typer.Exit(1)
    except LabError as exc:
        emit({"status": "FAIL", "code": exc.code, "message": exc.message,
              "data": exc.data, **recovery(exc.code, exc.message)})
        raise typer.Exit(1) from exc


@app.command()
def tools():
    """Print the capability registry and JSON input/output schemas."""
    emit(Registry(Store()).schemas())


@app.command()
def call(tool: str, arguments: str):
    """Invoke a registered capability with a strict JSON object."""
    try:
        values = json.loads(arguments)
        if not isinstance(values, dict):
            raise ValueError("Expected a JSON object")
    except ValueError as exc:
        emit({"status": "FAIL", "code": "INVALID_JSON", "message": str(exc)})
        raise typer.Exit(1) from exc
    invoke(tool, **values)


@app.command()
def doctor(profile: Profile = Profile.full, vm_count: int = typer.Option(3, min=1, max=3),
           json_output: bool = typer.Option(False, "--json")):
    """Read-only host checks, with pass/fail details and remediation."""
    rows = verify_host(Store(), profile.value, vm_count)
    if json_output:
        emit({"checks": rows, "host_ready": not any(r["status"] == "FAIL" for r in rows)})
    else:
        for row in rows:
            typer.echo(f"[{row['status']:4}] {row['name']}: {row['evidence']}")
            if row["remediation"]:
                typer.echo("       " + row["remediation"])
    raise typer.Exit(1 if any(row["status"] == "FAIL" for row in rows) else 0)


@app.command()
def validate(profile: Profile = Profile.full, vm_count: int = typer.Option(3, min=1, max=3),
             vm_timeout: int = typer.Option(240, min=1, max=600)):
    """Run all selected real checks and fault tests; always save a report and clean up."""
    _, code = Validation(Store(), profile.value, vm_count, vm_timeout).run()
    raise typer.Exit(code)


@app.command()
def report(details: bool = False, failures: bool = False):
    """Print the latest validation result and report paths."""
    store = Store()
    pointer = store.root / "reports/latest.json"
    if not pointer.exists():
        raise typer.BadParameter("No report yet; run labctl validate")
    latest = json.loads(pointer.read_text())
    run_id = latest.get("run_id", "")
    if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
        raise LabError("INVALID_STATE", "Latest report contains an invalid run ID")
    folder = store.root / "reports" / run_id
    result = json.loads((folder / "report.json").read_text())
    if details or failures:
        if failures:
            result = {"run_id": run_id, "status": result["status"],
                      "environment_ready": result["environment_ready"],
                      "checks": [r for r in result["checks"] if r["status"] in ("FAIL", "SKIP")],
                      "report_directory": str(folder)}
        emit(result)
        return
    emit({**latest, **{k: result.get(k) for k in ("started_at", "finished_at", "profile", "counts", "first_failure")},
          "json": str(folder / "report.json"), "html": str(folder / "report.html"),
          "markdown": str(folder / "summary.md")})


@app.command()
def diagnose(output_dir: Optional[Path] = None):
    """Collect bounded diagnostic evidence locally, including when runtime state is broken."""
    runtime = Path(os.environ.get("LAB_ROOT", Path.home() / ".local/share/generic-agent-lab"))
    source = Path(os.environ.get("LAB_SOURCE_ROOT", Path(__file__).resolve().parents[1]))
    emit(collect(runtime, output_dir, source=source))


@app.command()
def cleanup(job_id: Optional[str] = None):
    """Remove owned containers and VMs; retain logs, job history, and cached base image."""
    store = Store()
    registry = Registry(store)
    jobs = [job_id] if job_id else store.jobs()
    results = []
    for job in jobs:
        try:
            for folder in sorted((store.job(job) / "vm").glob("VM-*")):
                results.append(registry.call("vm_destroy", {"job_id": job, "vm_id": folder.name}).model_dump())
        except (LabError, OSError, ValueError) as exc:
            results.append({"status": "FAIL", "job_id": job, "code": error_code(exc), "message": str(exc)})
        results.append(registry.call("sandbox_destroy", {"job_id": job}).model_dump())
    emit(results)
    raise typer.Exit(1 if any(r["status"] == "FAIL" for r in results) else 0)


job_app = typer.Typer(no_args_is_help=True)
sandbox_app = typer.Typer(no_args_is_help=True)
file_app = typer.Typer(no_args_is_help=True)
git_app = typer.Typer(no_args_is_help=True)
patch_app = typer.Typer(no_args_is_help=True)
build_app = typer.Typer(no_args_is_help=True)
image_app = typer.Typer(no_args_is_help=True)
vm_app = typer.Typer(no_args_is_help=True)
test_app = typer.Typer(no_args_is_help=True)
fault_app = typer.Typer(no_args_is_help=True)
for name, group in (("job", job_app), ("sandbox", sandbox_app), ("file", file_app), ("git", git_app),
                    ("patch", patch_app), ("build", build_app), ("image", image_app), ("vm", vm_app),
                    ("test", test_app), ("fault", fault_app)):
    app.add_typer(group, name=name)


@job_app.command("create")
def job_create():
    invoke("job_create")


@job_app.command("status")
def job_status(job_id: str):
    invoke("job_status", job_id=job_id)


@job_app.command("list")
def job_list():
    emit({"jobs": Store().jobs()})


@sandbox_app.command("create")
def sandbox_create(job_id: str):
    invoke("sandbox_create", job_id=job_id)


@sandbox_app.command("destroy")
def sandbox_destroy(job_id: str):
    invoke("sandbox_destroy", job_id=job_id)


@file_app.command("read")
def file_read(job_id: str, path: str):
    invoke("file_read", job_id=job_id, path=path)


@file_app.command("list")
def file_list(job_id: str, path: str = "."):
    invoke("file_list", job_id=job_id, path=path)


@file_app.command("find")
def file_find(job_id: str, query: str):
    invoke("file_find", job_id=job_id, query=query)


@git_app.command("init")
def git_init(job_id: str, source_id: str):
    invoke("git_init", job_id=job_id, source_id=source_id)


@git_app.command("status")
def git_status(job_id: str, source_id: str):
    invoke("git_status", job_id=job_id, source_id=source_id)


@git_app.command("show")
def git_show(job_id: str, source_id: str, commit: str = "HEAD"):
    invoke("git_show", job_id=job_id, source_id=source_id, commit=commit)


@git_app.command("log")
def git_log(job_id: str, source_id: str, query: Optional[str] = None):
    invoke("git_log", job_id=job_id, source_id=source_id, query=query)


@git_app.command("diff")
def git_diff(job_id: str, source_id: str, a: str = "HEAD", b: Optional[str] = None):
    invoke("git_diff", job_id=job_id, source_id=source_id, a=a, b=b)


@patch_app.command("create")
def patch_create(job_id: str, source_id: str, variant: str = "fix"):
    invoke("patch_create", job_id=job_id, source_id=source_id, variant=variant)


@patch_app.command("check")
def patch_check(job_id: str, source_id: str, patch_id: str):
    invoke("patch_check", job_id=job_id, source_id=source_id, patch_id=patch_id)


@patch_app.command("apply")
def patch_apply(job_id: str, source_id: str, patch_id: str):
    invoke("patch_apply", job_id=job_id, source_id=source_id, patch_id=patch_id)


@build_app.command("run")
def build_run(job_id: str, source_id: str, profile: str = "python-toy"):
    invoke("build_run", job_id=job_id, source_id=source_id, profile=profile)


@build_app.command("status")
def build_status(job_id: str):
    invoke("build_status", job_id=job_id)


@image_app.command("prepare")
def image_prepare():
    try:
        emit(VMBroker(Store()).prepare_base())
    except LabError as exc:
        emit({"status": "FAIL", "code": exc.code, "message": exc.message, "data": exc.data})
        raise typer.Exit(1) from exc


@image_app.command("register")
def image_register(job_id: str):
    invoke("image_register", job_id=job_id)


@vm_app.command("deploy")
def vm_deploy(job_id: str, image_id: str, profile: str = "small"):
    invoke("vm_deploy", job_id=job_id, image_id=image_id, profile=profile)


@vm_app.command("status")
def vm_status(job_id: str, vm_id: str):
    invoke("vm_status", job_id=job_id, vm_id=vm_id)


@vm_app.command("wait")
def vm_wait(job_id: str, vm_id: str, timeout: int = 240):
    invoke("vm_wait", job_id=job_id, vm_id=vm_id, timeout=timeout)


@vm_app.command("destroy")
def vm_destroy(job_id: str, vm_id: str):
    invoke("vm_destroy", job_id=job_id, vm_id=vm_id)


@vm_app.command("reap")
def vm_reap():
    try:
        emit(VMBroker(Store()).reap())
    except LabError as exc:
        emit({"status": "FAIL", "code": exc.code, "message": exc.message})
        raise typer.Exit(1) from exc


@test_app.command("run")
def test_run(job_id: str, vm_id: str, image_id: str, suite: str = "smoke"):
    invoke("test_run", job_id=job_id, vm_id=vm_id, image_id=image_id, suite=suite)


@fault_app.command("set")
def fault_set(job_id: str, fault: str):
    invoke("fault_set", job_id=job_id, fault=fault)


@fault_app.command("clear")
def fault_clear(job_id: str):
    invoke("fault_clear", job_id=job_id)


if __name__ == "__main__":
    app()
