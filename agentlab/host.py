from __future__ import annotations

import fcntl
import json
import os
import platform
import pwd
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from .models import LabError
from .process import checked
from .recovery import error_code, recovery
from .store import Store
from .vm import URI, VM_RAM_MIB, VMBroker


def memory() -> dict[str, int]:
    return {line.split(":")[0]: int(line.split()[1]) * 1024 for line in
            Path("/proc/meminfo").read_text().splitlines() if len(line.split()) >= 2}


def verify_host(store: Store, profile: str = "full", vm_count: int = 3, on_check=None) -> list[dict]:
    checks: list[dict] = []

    def check(name: str, action, fix: str, required: bool = True):
        try:
            evidence = action()
            checks.append({"name": name, "status": "PASS", "required": required,
                           "evidence": evidence, "remediation": ""})
        except (LabError, OSError, ValueError, KeyError, AssertionError) as exc:
            detail = str(exc)
            if isinstance(exc, LabError):
                diagnostic = (exc.data.get("output", "") + exc.data.get("stderr", "")).strip()
                if diagnostic:
                    detail += ": " + diagnostic[-4000:]
            code = error_code(exc)
            advice = recovery(code, detail, step=name)
            checks.append({"name": name, "status": "FAIL" if required else "WARN", "required": required,
                           "evidence": detail, "code": code, "remediation": fix,
                           "commands": advice["commands"]})
        if on_check:
            on_check(checks[-1])

    def require(ok: bool, message: str):
        if not ok:
            raise LabError("HOST_CHECK_FAILED", message)
        return message

    os_release = dict(line.split("=", 1) for line in Path("/etc/os-release").read_text().splitlines()
                      if "=" in line)
    check("host.ubuntu", lambda: require(os_release.get("ID", "").strip('"') == "ubuntu" and
          os_release.get("VERSION_ID", "").strip('"') in ("22.04", "24.04"),
          "Supported host: Ubuntu 22.04/24.04; detected " + os_release.get("PRETTY_NAME", "unknown")),
          "Use an Ubuntu 22.04 or 24.04 x86_64 host for the full environment proof.", profile == "full")
    check("host.architecture", lambda: require(platform.machine() == "x86_64", platform.machine()),
          "The supplied guest profile targets x86_64.", profile == "full")
    check("host.nonroot", lambda: require(os.geteuid() != 0, f"Effective UID: {os.geteuid()}"),
          "Run with the dedicated account created by scripts/bootstrap-ubuntu.sh.")
    check("host.python", lambda: require(sys.version_info >= (3, 10), platform.python_version()),
          "Install Python 3.10 or newer.")
    if profile == "software":
        return checks
    for executable in ("podman", "git"):
        check(f"host.{executable}", lambda e=executable: require(bool(shutil.which(e)), e + " installed"),
              "Run the Ubuntu bootstrap.")

    def podman_info():
        info = json.loads(checked(["podman", "info", "--format", "json"], separate_stderr=True).output)
        require(info["host"]["security"]["rootless"], "Podman must run rootlessly")
        require(info["host"]["cgroupVersion"] == "v2", "Cgroups v2 is required for resource limits")
        controllers = info["host"].get("cgroupControllers", [])
        missing = sorted({"cpu", "memory", "pids"} - set(controllers))
        if missing:
            raise LabError("CGROUP_DELEGATION_MISSING", "Missing delegated controllers: " + ", ".join(missing))
        return {"rootless": True, "cgroup_version": "v2", "version": info["version"]["Version"],
                "controllers": controllers, "storage_driver": info["store"]["graphDriverName"]}
    check("host.rootless_podman", podman_info, "Run bootstrap and use a fresh login/session for the lab user.")
    username = pwd.getpwuid(os.getuid()).pw_name
    for kind in ("subuid", "subgid"):
        def subids(k=kind):
            rows = [line.split(":") for line in Path(f"/etc/{k}").read_text().splitlines()]
            return require(any(r[0] in (username, str(os.getuid())) and int(r[2]) >= 65536
                               for r in rows if len(r) == 3), f"{k}: need at least 65536 subordinate IDs")
        check(f"host.{kind}", subids, "Bootstrap allocates a non-overlapping range for the lab user.")
    check("host.sandbox_disk", lambda: require(shutil.disk_usage(store.root).free >= 3 * 1024**3,
          f"{shutil.disk_usage(store.root).free / 1024**3:.1f} GiB free; need 3 GiB for sandbox tests"),
          "Free space on the runtime/container-storage filesystem.")
    check("host.sandbox_memory", lambda: require(memory()["MemAvailable"] >= 700 * 1024**2,
          f"{memory()['MemAvailable'] / 1024**2:.0f} MiB available; need 700 MiB"), "Free host memory.")
    if profile == "sandbox":
        return checks

    check("host.cpu", lambda: require((os.cpu_count() or 0) >= 4, f"{os.cpu_count()} logical CPUs; need 4"),
          "Allocate at least four logical CPUs.")
    check("host.total_memory", lambda: require(memory()["MemTotal"] >= 15 * 1024**3,
          f"{memory()['MemTotal'] / 1024**3:.1f} GiB RAM; target 16 GB"), "Use a host with at least 16 GB RAM.")
    needed = (VM_RAM_MIB * vm_count + 1024) * 1024**2
    check("host.available_memory", lambda: require(memory()["MemAvailable"] >= needed,
          f"{memory()['MemAvailable'] / 1024**3:.1f} GiB available; need {needed / 1024**3:.1f} GiB"),
          "Stop other workloads or lower --vm-count for a smaller scoped proof.")
    vm = VMBroker(store)
    check("host.vm_storage", lambda: (vm.init_storage() or "Lab-owned VM storage ready"),
          "Run scripts/bootstrap-ubuntu.sh to provision the storage directory.")
    for label, path in (("runtime", store.root), ("vm", vm.storage)):
        check(f"host.disk_{label}", lambda p=path: require(shutil.disk_usage(p).free >= 80 * 1024**3,
              f"{shutil.disk_usage(p).free / 1024**3:.1f} GiB free; target 80 GiB"),
              "Free space or move the lab onto a disk with at least 80 GiB free.")

    def kvm():
        with open("/dev/kvm", "rb+", buffering=0) as device:
            api = fcntl.ioctl(device, 0xAE00, 0)  # KVM_GET_API_VERSION
            require(api == 12, f"KVM API version {api}")
            guest_fd = fcntl.ioctl(device, 0xAE01, 0)  # KVM_CREATE_VM; no guest is booted.
            os.close(guest_fd)
        return "Opened /dev/kvm and created/closed a KVM VM descriptor"
    check("host.kvm", kvm, "Enable VT-x/AMD-V or nested virtualization; grant the lab user kvm access.")
    for executable in ("virsh", "virt-install", "qemu-img", "cloud-localds", "osinfo-query",
                       "ssh", "scp", "ssh-keygen", "curl", "gpgv"):
        check(f"host.{executable}", lambda e=executable: require(bool(shutil.which(e)), e + " installed"),
              "Run scripts/bootstrap-ubuntu.sh.")
    check("host.libvirt", lambda: checked(["virsh", "-c", URI, "list", "--all"]).output,
          "Start libvirtd and run in a fresh session with libvirt group membership.")

    def network():
        active = checked(["virsh", "-c", URI, "net-list", "--name"]).output.splitlines()
        require("default" in active, "Default network must be active")
        xml = ET.fromstring(checked(["virsh", "-c", URI, "net-dumpxml", "default"], separate_stderr=True).output)
        require(xml.find("./ip/dhcp/range") is not None, "Default network must provide DHCP")
        forward = xml.find("forward")
        require(forward is not None and forward.get("mode") == "nat", "Default network must use NAT")
        return "Active default NAT network with DHCP"
    check("host.libvirt_network", network, "Bootstrap starts/autostarts default NAT; resolve subnet conflicts if it fails.")
    check("host.cloud_image_keyring", lambda: require(Path(
          "/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg").is_file(), "Ubuntu cloud image signature keyring"),
          "Install ubuntu-cloudimage-keyring.")
    check("host.vm_expiry_timer", lambda: checked([
          "systemctl", "is-active", "generic-agent-lab-reaper.timer"]).output.strip(),
          "Enable the expiry timer with scripts/bootstrap-ubuntu.sh.")
    return checks
