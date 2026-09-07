from __future__ import annotations

import json
from typing import Callable

from pydantic import ValidationError

from . import models as m
from .build import BuildBroker
from .local_tools import LocalTools
from .recovery import error_code, recovery
from .sandbox import Sandbox
from .store import Store
from .vm import TestBroker, VMBroker


class Registry:
    """Capability API: strict input validation before any broker operation."""
    def __init__(self, store: Store):
        self.store = store
        local, sandbox = LocalTools(store), Sandbox(store)
        build, vm, test = BuildBroker(store), VMBroker(store), TestBroker(store)
        self.tools: dict[str, tuple[type[m.StrictModel], Callable]] = {
            "job_create": (m.EmptyInput, store.create_job),
            "job_status": (m.JobInput, self.job_status),
            "sandbox_create": (m.JobInput, sandbox.create),
            "sandbox_destroy": (m.JobInput, sandbox.destroy),
            "file_read": (m.FileInput, local.file_read),
            "file_list": (m.FileInput, local.file_list),
            "file_find": (m.FindInput, local.file_find),
            "git_init": (m.SourceInput, local.git_init),
            "git_status": (m.SourceInput, local.git_status),
            "git_show": (m.ShowInput, local.git_show),
            "git_log": (m.LogInput, local.git_log),
            "git_diff": (m.DiffInput, local.git_diff),
            "patch_create": (m.PatchCreateInput, local.patch_create),
            "patch_check": (m.PatchInput, local.patch_check),
            "patch_apply": (m.PatchInput, local.patch_apply),
            "build_run": (m.BuildInput, build.run),
            "build_status": (m.JobInput, build.current),
            "image_register": (m.JobInput, vm.image_register),
            "vm_deploy": (m.DeployInput, vm.deploy),
            "vm_status": (m.VMInput, vm.status),
            "vm_wait": (m.WaitInput, vm.wait),
            "test_run": (m.TestInput, test.run),
            "vm_destroy": (m.VMInput, vm.destroy),
            "fault_set": (m.FaultInput, self.fault_set),
            "fault_clear": (m.JobInput, self.fault_clear),
        }

    def job_status(self, job_id: str) -> dict:
        state = self.store.state(job_id)
        if state.current_build_id:
            try:
                BuildBroker(self.store).current(job_id)
            except m.LabError:
                self.store.invalidate(job_id)
        return self.store.state(job_id).model_dump()

    def fault_set(self, job_id: str, fault: str) -> dict:
        state = self.store.state(job_id)
        state.fault = fault
        self.store.save(state)
        return {"fault": fault, "simulated": True}

    def fault_clear(self, job_id: str) -> dict:
        state = self.store.state(job_id)
        state.fault = None
        self.store.save(state)
        return {"fault": None}

    def schemas(self) -> dict:
        return {name: {"input": schema.model_json_schema(),
                       "output": m.ToolResult.model_json_schema()} for name, (schema, _) in self.tools.items()}

    def call(self, tool: str, arguments: dict) -> m.ToolResult:
        try:
            result = self._call(tool, arguments)
        except (OSError, ValueError, KeyError) as exc:
            result = m.ToolResult(tool=tool, status="FAIL", code=error_code(exc), message=str(exc))
        if result.status == "FAIL":
            result.data.update(recovery(result.code, result.message + " " + str(result.data)))
        return result

    def _call(self, tool: str, arguments: dict) -> m.ToolResult:
        if tool not in self.tools:
            return m.ToolResult(tool=tool, status="FAIL", code="UNKNOWN_TOOL", message="Unknown capability")
        schema, handler = self.tools[tool]
        try:
            request = schema.model_validate(arguments)
        except ValidationError as exc:
            return m.ToolResult(tool=tool, status="FAIL", code="INVALID_INPUT",
                                message="Arguments do not match the strict tool schema",
                                data={"errors": json.loads(exc.json(include_input=False, include_url=False))})
        args = request.model_dump()
        job_id = args.get("job_id")

        def invoke():
            try:
                result = m.ToolResult(tool=tool, status="PASS", code="OK", data=handler(**args))
            except m.LabError as exc:
                result = m.ToolResult(tool=tool, status="FAIL", code=exc.code,
                                      message=exc.message, data=exc.data)
            except (OSError, ValueError, KeyError) as exc:
                result = m.ToolResult(tool=tool, status="FAIL", code=error_code(exc),
                                      message=str(exc))
            if job_id:
                # Event metadata is outside the writable sandbox mount.
                try:
                    self.store.event(job_id, tool.upper(), status=result.status, code=result.code,
                                     evidence={k: v for k, v in result.data.items() if k.endswith("_id")},
                                     simulated=result.data.get("simulated", False))
                except OSError as exc:
                    if result.status == "FAIL":
                        result.data["event_logging_error"] = str(exc)
                    else:
                        result = m.ToolResult(tool=tool, status="FAIL", code="EVENT_LOG_FAILED",
                            message="Operation succeeded but its event could not be recorded; inspect before retrying",
                            data={"operation_completed": True, "operation_result": result.data,
                                  "event_logging_error": str(exc)})
            return result
        try:
            if job_id:
                self.store.job(job_id)
                with self.store.lock(job_id):
                    return invoke()
            return invoke()
        except m.LabError as exc:
            return m.ToolResult(tool=tool, status="FAIL", code=exc.code, message=exc.message)
