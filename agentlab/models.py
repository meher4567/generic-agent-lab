from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

JobID = Annotated[str, StringConstraints(pattern=r"^JOB-[0-9a-f]{32}$")]
SourceID = Annotated[str, StringConstraints(pattern=r"^SOURCE-[0-9a-f]{32}$")]
PatchID = Annotated[str, StringConstraints(pattern=r"^PATCH-[0-9a-f]{32}$")]
VMID = Annotated[str, StringConstraints(pattern=r"^VM-[0-9a-f]{32}$")]
ImageID = Annotated[str, StringConstraints(pattern=r"^IMAGE-[0-9a-f]{64}$")]
GitRef = Annotated[str, StringConstraints(pattern=r"^(HEAD|[0-9a-f]{40})$")]
Fault = Literal[
    "BUILD_TIMEOUT", "DISK_FULL", "DEPENDENCY_FAILURE", "NO_IP", "SSH_TIMEOUT",
    "BOOT_TIMEOUT", "HYPERVISOR_UNAVAILABLE", "STALE_VM_ID", "WRONG_IMAGE_BINDING",
    "SSH_DISCONNECT",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class JobInput(StrictModel):
    job_id: JobID


class EmptyInput(StrictModel):
    pass


class FileInput(JobInput):
    path: str = Field(min_length=1, max_length=512)


class FindInput(JobInput):
    query: str = Field(min_length=1, max_length=128)


class SourceInput(JobInput):
    source_id: SourceID


class ShowInput(SourceInput):
    commit: GitRef = "HEAD"


class LogInput(SourceInput):
    query: str | None = Field(default=None, max_length=128)


class DiffInput(SourceInput):
    a: GitRef = "HEAD"
    b: GitRef | None = None


class PatchCreateInput(SourceInput):
    variant: Literal["fix", "next", "invalid"] = "fix"


class PatchInput(SourceInput):
    patch_id: PatchID


class BuildInput(SourceInput):
    profile: Literal["python-toy"] = "python-toy"


class DeployInput(JobInput):
    image_id: ImageID
    profile: Literal["small"] = "small"


class VMInput(JobInput):
    vm_id: VMID


class WaitInput(VMInput):
    timeout: int = Field(default=240, ge=1, le=600)


class TestInput(VMInput):
    image_id: ImageID
    suite: Literal["smoke"] = "smoke"


class FaultInput(JobInput):
    fault: Fault


class CommandResult(StrictModel):
    returncode: int
    output: str
    stderr: str = ""
    duration: float
    timed_out: bool = False
    output_limited: bool = False


class ToolResult(StrictModel):
    tool: str
    status: Literal["PASS", "FAIL"]
    code: str
    data: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class JobState(StrictModel):
    job_id: JobID
    status: Literal["CREATED", "READY", "RUNNING", "BLOCKED", "FAILED", "COMPLETE"]
    current_source_id: str
    current_patch_id: str | None = None
    current_build_id: str | None = None
    current_image_id: str | None = None
    current_vm_id: str | None = None
    build_status: str = "NOT_RUN"
    current_source_hash: str | None = None
    fault: str | None = None
    created_at: str


class LabError(Exception):
    def __init__(self, code: str, message: str, **data: Any):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data
