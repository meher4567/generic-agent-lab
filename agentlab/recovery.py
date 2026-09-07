"""Operator recovery advice. Commands are suggestions, never executed automatically."""
from __future__ import annotations

import errno
import os

from .models import LabError


def operator_command() -> str:
    return "sudo agentlab" if os.environ.get("LAB_SOURCE_ROOT") == "/opt/generic-agent-lab/source" else "labctl"


def error_code(exc: Exception) -> str:
    if isinstance(exc, LabError):
        return exc.code
    if isinstance(exc, OSError):
        return {errno.ENOSPC: "DISK_FULL", errno.EDQUOT: "DISK_FULL", errno.EACCES: "PERMISSION_DENIED",
                errno.EPERM: "PERMISSION_DENIED", errno.EROFS: "READ_ONLY_FILESYSTEM"}.get(
                    exc.errno, "FILESYSTEM_ERROR")
    if isinstance(exc, (ValueError, KeyError)):
        return "INVALID_STATE"
    return "INTERNAL_ERROR"


def recovery(code: str, message: str = "", *, step: str = "") -> dict:
    cli = operator_command()
    inspect = [f"{cli} report --failures", f"{cli} diagnose"]
    advice = "Inspect the first failed check and its saved evidence before retrying."
    commands = inspect
    text = message.lower()
    if code == "DISK_FULL" or any(s in text for s in ("no space left", "disk quota exceeded")):
        advice = "Check free bytes and inodes. Free space outside active lab disks, then rerun the failed command."
        commands = ["df -h", "df -i", *inspect]
    elif code == "RESOURCE_BUSY":
        advice = "Another lab operation is still using this resource. Let it finish, then retry; avoid starting concurrent setup or validation commands."
    elif any(s in text for s in ("could not resolve", "temporary failure resolving", "name resolution")):
        advice = "DNS lookup failed. Check the host DNS/proxy connection, then repeat setup or validation."
        commands = ["getent hosts archive.ubuntu.com pypi.org cloud-images.ubuntu.com", *inspect]
    elif any(s in text for s in ("certificate verify", "certificate problem", "certificate verification")):
        advice = "Check the system clock and trusted CA configuration, including any proxy CA. Keep TLS verification enabled."
        commands = ["timedatectl status", *inspect]
    elif code in {"PERMISSION_DENIED", "ROOT_DENIED", "ROOTLESS_REQUIRED"}:
        advice = "Use the dedicated account through sudo agentlab. For local mode, check ownership of LAB_ROOT and run as its non-root owner."
        commands = ["id", *inspect]
    elif code in {"INVALID_STATE", "EVENT_LOG_FAILED"}:
        advice = "State or event recording failed. Preserve the runtime and inspect existing resources before retrying an operation that may already have completed."
    elif code in {"OWNERSHIP_MISMATCH", "STORAGE_OWNERSHIP_MISMATCH"}:
        advice = "Restore the matching LAB_ROOT and LAB_VM_STORAGE configuration. Preserve ownership records; do not remove them to bypass this check."
    elif code in {"NO_IP", "SSH_TIMEOUT", "BOOT_TIMEOUT", "VM_STOPPED", "SSH_DISCONNECT"}:
        advice = "Read the saved readiness evidence: VM state, DHCP address, last SSH result, and cloud-init output when reachable. Check libvirt/NAT first."
        commands = ["sudo systemctl status libvirtd --no-pager", f"{cli} doctor",
                    f"{cli} validate --vm-timeout 600", *inspect]
    elif code in {"BUILD_TIMEOUT", "OUTPUT_LIMIT"}:
        advice = "Inspect the saved build output and host memory. A new validation run creates fresh jobs and sandboxes."
        commands = ["free -h", *inspect]
    elif code in {"STALE_BUILD", "WRONG_IMAGE_BINDING", "SOURCE_MISMATCH", "STALE_VM_ID"}:
        advice = "Use IDs from the same job's current source/build/image. Run validation again for a fresh, consistently bound workflow."
    elif code in {"VM_QUOTA", "CLEANUP_FAILED"} or step.startswith("cleanup."):
        advice = "Restore hypervisor access, then retry owned-resource cleanup. Preserve the runtime and VM disks until cleanup succeeds."
        commands = [f"{cli} doctor", f"{cli} cleanup", *inspect]
    elif code in {"BASE_HASH_MISMATCH", "IMAGE_MANIFEST_INVALID", "IMAGE_KEYRING_MISSING"}:
        advice = "Inspect the signed-image error and Ubuntu keyring. A download failure can be retried; an altered cached base needs investigation before replacement."
        commands = [f"{cli} image prepare", *inspect]
    elif code == "MISSING_DEPENDENCY":
        advice = "Repeat scripts/start.sh on Ubuntu to install the missing dependency, then rerun validation."
    elif code == "REPORT_WRITE_FAILED":
        advice = "Report persistence failed. Check disk space, inodes, and permissions. Use the emergency report path printed in the terminal; the latest pointer may refer to an older run."
        commands = ["df -h", "df -i"]
    elif code == "INTERRUPTED":
        advice = "The run was interrupted and cleanup was attempted. Check cleanup results, then rerun validation when ready."
        commands = [f"{cli} cleanup", *inspect]
    elif code == "BUILD_FAILED":
        advice = "Read the saved build output. The toy source initially fails by design; the normal validation applies its patch before the successful build."
    return {"remediation": advice, "commands": commands}
