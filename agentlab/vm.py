from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .build import BuildBroker
from .diagnostics import redact
from .models import LabError
from .policy import read_file, sha256
from .process import checked, run
from .recovery import error_code
from .store import Store, atomic_json, new_id, now

URI = "qemu:///system"
OWNER_NS = "urn:generic-agent-lab:owner:v1"
VM_RAM_MIB = 1536
MAX_VMS = 3
MAX_LIFETIME = 3600
VM_FAULTS = {"NO_IP", "SSH_TIMEOUT", "BOOT_TIMEOUT", "HYPERVISOR_UNAVAILABLE",
             "STALE_VM_ID", "WRONG_IMAGE_BINDING", "SSH_DISCONNECT"}
BASE_URL = "https://cloud-images.ubuntu.com/noble/current/"
BASE_FILE = "noble-server-cloudimg-amd64.img"


def virsh(*args: str, timeout: float = 30):
    return checked(["virsh", "--connect", URI, *args], timeout=timeout, separate_stderr=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VMBroker:
    def __init__(self, store: Store):
        self.store = store
        self.storage = Path(os.environ.get("LAB_VM_STORAGE",
                             "/var/lib/libvirt/images/generic-agent-lab")).absolute()

    def init_storage(self) -> None:
        if (not self.storage.is_dir() or self.storage.is_symlink()
                or self.storage.stat().st_uid != os.getuid()):
            raise LabError("VM_STORAGE_UNAVAILABLE", "Run the Ubuntu bootstrap to provision VM storage")
        owner = self.storage / ".lab-owner.json"
        if owner.exists():
            if json.loads(owner.read_text())["instance"] != self.store.instance:
                raise LabError("STORAGE_OWNERSHIP_MISMATCH", "VM storage belongs to another lab runtime")
        else:
            atomic_json(owner, {"instance": self.store.instance})

    def prepare_base(self) -> dict:
        with self.store.lock("vm-global"):
            self.init_storage()
            manifest_path = self.storage / "base.json"
            if manifest_path.exists():
                record = json.loads(manifest_path.read_text())
                path = self.storage / f"base-{record['sha256']}.qcow2"
                if path.is_symlink() or file_hash(path) != record["sha256"]:
                    raise LabError("BASE_HASH_MISMATCH", "Cached cloud image has changed")
                return record
            keyring = Path("/usr/share/keyrings/ubuntu-cloudimage-keyring.gpg")
            if not keyring.exists():
                raise LabError("IMAGE_KEYRING_MISSING", "Install ubuntu-cloudimage-keyring first")
            staging = self.storage / f"download-{uuid.uuid4().hex}"
            staging.mkdir(mode=0o700)
            try:
                for name in ("SHA256SUMS", "SHA256SUMS.gpg"):
                    checked(["curl", "--fail", "--location", "--silent", "--show-error",
                             "--proto", "=https", "--proto-redir", "=https", "--retry", "3",
                             "--connect-timeout", "20", "--max-time", "120", "--output",
                             str(staging / name), BASE_URL + name], timeout=150)
                checked(["gpgv", "--keyring", str(keyring), str(staging / "SHA256SUMS.gpg"),
                         str(staging / "SHA256SUMS")])
                entries = dict((line.split()[1].lstrip("*"), line.split()[0]) for line in
                               (staging / "SHA256SUMS").read_text().splitlines() if len(line.split()) == 2)
                expected = entries.get(BASE_FILE, "")
                if not re.fullmatch(r"[0-9a-f]{64}", expected):
                    raise LabError("IMAGE_MANIFEST_INVALID", "Official signed manifest lacks the image")
                image = staging / BASE_FILE
                checked(["curl", "--fail", "--location", "--silent", "--show-error", "--proto",
                         "=https", "--proto-redir", "=https", "--retry", "3", "--connect-timeout",
                         "20", "--max-time", "1800", "--speed-limit", "1024", "--speed-time", "60",
                         "--max-filesize", "2147483648", "--output", str(image), BASE_URL + BASE_FILE],
                        timeout=1900)
                if file_hash(image) != expected:
                    raise LabError("BASE_HASH_MISMATCH", "Cloud image does not match signed SHA256SUMS; retry")
                info = json.loads(checked(["qemu-img", "info", "--output=json", str(image)], separate_stderr=True).output)
                if info["format"] != "qcow2" or info.get("backing-filename"):
                    raise LabError("IMAGE_FORMAT_DENIED", "Expected a standalone qcow2 base")
                image.chmod(0o444)
                image.replace(self.storage / f"base-{expected}.qcow2")
                record = {"sha256": expected, "url": BASE_URL + BASE_FILE, "signature_verified": True,
                          "downloaded_at": now(), "virtual_size": info["virtual-size"]}
                shutil.copyfile(staging / "SHA256SUMS", self.storage / "SHA256SUMS")
                shutil.copyfile(staging / "SHA256SUMS.gpg", self.storage / "SHA256SUMS.gpg")
                atomic_json(manifest_path, record)
                return record
            finally:
                shutil.rmtree(staging)

    def image_register(self, job_id: str) -> dict:
        build = BuildBroker(self.store).current(job_id)
        self.init_storage()
        base_path = self.storage / "base.json"
        if not base_path.exists():
            raise LabError("BASE_NOT_PREPARED", "Run labctl image prepare first")
        base = json.loads(base_path.read_text())
        manifest = {"job_id": job_id, "build_id": build["build_id"],
                    "artifact_id": build["artifact_id"], "artifact_sha256": build["artifact_sha256"],
                    "base_sha256": base["sha256"]}
        image_id = "IMAGE-" + sha256(json.dumps(manifest, sort_keys=True).encode())
        record = {**manifest, "image_id": image_id}
        atomic_json(self.store.job(job_id) / "images" / f"{image_id}.json", record)
        state = self.store.state(job_id)
        state.current_image_id = image_id
        self.store.save(state)
        return record

    def image(self, job_id: str, image_id: str) -> dict:
        path = self.store.job(job_id) / "images" / f"{image_id}.json"
        if not path.exists():
            raise LabError("UNKNOWN_IMAGE", "Image is not registered in this job")
        record = json.loads(path.read_text())
        build = BuildBroker(self.store).current(job_id)
        if (record["build_id"] != build["build_id"] or
                record["artifact_sha256"] != build["artifact_sha256"] or
                self.store.state(job_id).current_image_id != image_id):
            raise LabError("WRONG_IMAGE_BINDING", "Image is not bound to the current passing build")
        return record

    def record(self, job_id: str, vm_id: str) -> dict:
        if not re.fullmatch(r"VM-[0-9a-f]{32}", vm_id):
            raise LabError("INVALID_ID", "Malformed VM ID")
        path = self.store.job(job_id) / "vm" / vm_id / "record.json"
        if not path.exists():
            raise LabError("STALE_VM_ID", "VM is not registered in this job")
        record = json.loads(path.read_text())
        if record["job_id"] != job_id or record["instance"] != self.store.instance:
            raise LabError("OWNERSHIP_MISMATCH", "VM belongs to another job or installation")
        return record

    def save_record(self, record: dict) -> None:
        atomic_json(self.store.job(record["job_id"]) / "vm" / record["vm_id"] / "record.json", record)

    def domain_xml(self, record: dict) -> ET.Element | None:
        # An unavailable hypervisor must never be interpreted as an absent VM.
        domains = virsh("list", "--all", "--name").output.splitlines()
        if record["domain"] not in domains:
            return None
        xml = ET.fromstring(virsh("dumpxml", record["domain"]).output)
        marker = xml.find(f"./metadata/{{{OWNER_NS}}}owner")
        if (marker is None or marker.attrib != {"instance": self.store.instance,
                "job": record["job_id"], "vm": record["vm_id"], "image": record["image_id"]}
                or xml.findtext("uuid") != record["uuid"]):
            raise LabError("OWNERSHIP_MISMATCH", "Domain metadata or UUID does not match broker state")
        return xml

    def records(self) -> list[dict]:
        records = []
        for job_id in self.store.jobs():
            for path in (self.store.job(job_id) / "vm").glob("VM-*/record.json"):
                records.append(self.record(job_id, path.parent.name))
        return records

    def deploy(self, job_id: str, image_id: str, profile: str) -> dict:
        if profile != "small":
            raise LabError("PROFILE_DENIED", "Only the small VM profile is allowed")
        fault = self.store.state(job_id).fault
        if fault == "HYPERVISOR_UNAVAILABLE":
            raise LabError(fault, "Controlled hypervisor failure", simulated=True)
        image = self.image(job_id, image_id)
        with self.store.lock("vm-global"):
            self.init_storage()
            virsh("list", "--all", "--name")
            active = [r for r in self.records() if r["status"] != "DESTROYED"]
            if len(active) >= MAX_VMS:
                raise LabError("VM_QUOTA", "Maximum of three lab VMs; clean up existing VMs first")
            base = self.storage / f"base-{image['base_sha256']}.qcow2"
            if base.is_symlink() or file_hash(base) != image["base_sha256"]:
                raise LabError("BASE_HASH_MISMATCH", "Immutable base digest changed")
            vm_id = new_id("VM")
            vm_dir = self.store.job(job_id) / "vm" / vm_id
            vm_dir.mkdir(mode=0o700)
            disk_dir = self.storage / vm_id
            disk_dir.mkdir(mode=0o711)
            disk_dir.chmod(0o711)
            vm_uuid = str(uuid.uuid4())
            record = {"vm_id": vm_id, "job_id": job_id, "image_id": image_id,
                      "instance": self.store.instance, "uuid": vm_uuid,
                      "domain": f"gal-{self.store.instance[:8]}-{vm_id[3:]}",
                      "mac": "52:54:00:" + ":".join(f"{b:02x}" for b in os.urandom(3)),
                      "status": "PREPARING", "created_at": now(), "profile": profile,
                      "ram_mib": VM_RAM_MIB, "vcpus": 1, "max_lifetime_seconds": MAX_LIFETIME}
            self.save_record(record)  # Journal before creating any hypervisor resources.
            try:
                for key in ("client_key", "host_key"):
                    checked(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "lab-only",
                             "-f", str(vm_dir / key)])
                    (vm_dir / key).chmod(0o600)
                marker = {"job_id": job_id, "vm_id": vm_id, "image_id": image_id,
                          "artifact_sha256": image["artifact_sha256"]}
                cloud_config = {
                    "users": [{"name": "lab", "shell": "/bin/bash", "lock_passwd": True,
                               "ssh_authorized_keys": [(vm_dir / "client_key.pub").read_text().strip()]}],
                    "disable_root": True, "ssh_pwauth": False,
                    "ssh_keys": {"ed25519_private": (vm_dir / "host_key").read_text(),
                                 "ed25519_public": (vm_dir / "host_key.pub").read_text().strip()},
                    "write_files": [{"path": "/etc/lab-binding.json", "permissions": "0444",
                                     "content": json.dumps(marker)}],
                    "runcmd": [["touch", "/var/lib/cloud/instance/lab-ready"]],
                    # Guest also shuts itself down if the host-side reaper is interrupted.
                    "power_state": {"mode": "poweroff", "delay": "+60", "timeout": 30,
                                    "condition": True},
                }
                user_data = vm_dir / "user-data"
                user_data.write_text("#cloud-config\n" + json.dumps(cloud_config, indent=2) + "\n")
                user_data.chmod(0o600)
                (vm_dir / "meta-data").write_text(json.dumps({"instance-id": vm_id,
                                                             "local-hostname": "lab-guest"}))
                checked(["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(base),
                         str(disk_dir / "overlay.qcow2"), "12G"])
                checked(["cloud-localds", str(disk_dir / "seed.iso"), str(user_data),
                         str(vm_dir / "meta-data")])
                (disk_dir / "seed.iso").chmod(0o600)
                (disk_dir / "overlay.qcow2").chmod(0o600)
                os_list = checked(["osinfo-query", "os"]).output
                variant = next((name for name in ("ubuntu24.04", "ubuntu22.04") if name in os_list), None)
                if not variant:
                    raise LabError("OSINFO_MISSING", "Update osinfo-db; Ubuntu 22.04/24.04 profile is required")
                result = checked([
                    "virt-install", "--connect", URI, "--name", record["domain"], "--uuid", vm_uuid,
                    "--memory", str(VM_RAM_MIB), "--vcpus", "1", "--cpu", "host-model",
                    "--virt-type", "kvm", "--arch", "x86_64", "--import", "--os-variant", variant,
                    "--disk", f"path={disk_dir / 'overlay.qcow2'},format=qcow2,bus=virtio",
                    "--disk", f"path={disk_dir / 'seed.iso'},device=cdrom",
                    "--network", f"network=default,model=virtio,mac={record['mac']}",
                    "--graphics", "none", "--noautoconsole", "--print-xml",
                ], timeout=60, separate_stderr=True)
                xml = ET.fromstring(result.output)
                metadata = xml.find("metadata")
                if metadata is None:
                    metadata = ET.SubElement(xml, "metadata")
                ET.SubElement(metadata, f"{{{OWNER_NS}}}owner", {"instance": self.store.instance,
                    "job": job_id, "vm": vm_id, "image": image_id})
                definition = vm_dir / "domain.xml"
                definition.write_text(ET.tostring(xml, encoding="unicode"))
                virsh("define", str(definition))
                virsh("start", record["domain"], timeout=90)
                record["status"] = "RUNNING"
                self.save_record(record)
                state = self.store.state(job_id)
                state.current_vm_id = vm_id
                self.store.save(state)
                return {k: v for k, v in record.items() if k != "instance"}
            except BaseException as original:
                record["status"] = "FAILED"
                secondary_errors = []
                try:
                    self.save_record(record)
                except Exception as exc:
                    secondary_errors.append(f"state recording: {exc}")
                try:
                    self._destroy(record)
                except Exception as exc:
                    secondary_errors.append(f"cleanup: {exc}")
                if isinstance(original, LabError):
                    original.data.update(vm_id=vm_id, job_id=job_id, secondary_errors=secondary_errors)
                elif secondary_errors and isinstance(original, Exception):
                    raise LabError(error_code(original), str(original), vm_id=vm_id,
                                   secondary_errors=secondary_errors) from original
                raise

    def status(self, job_id: str, vm_id: str) -> dict:
        record = self.record(job_id, vm_id)
        xml = self.domain_xml(record)
        if xml is None or record["status"] == "DESTROYED":
            raise LabError("STALE_VM_ID", "VM no longer exists")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["created_at"])).total_seconds()
        if age >= MAX_LIFETIME:
            self.destroy(job_id, vm_id)
            raise LabError("VM_EXPIRED", "VM reached its maximum lifetime and was cleaned up")
        output = virsh("domifaddr", record["domain"], "--source", "lease").output
        ip = None
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 4 and fields[1].lower() == record["mac"] and fields[2] == "ipv4":
                candidate = ipaddress.ip_interface(fields[3]).ip
                network_xml = ET.fromstring(virsh("net-dumpxml", "default").output)
                allowed = [ipaddress.ip_network(f"{node.attrib['address']}/{node.attrib.get('netmask', node.attrib.get('prefix', '24'))}", strict=False)
                           for node in network_xml.findall("ip") if node.attrib.get("family", "ipv4") == "ipv4"]
                if candidate.is_loopback or not any(candidate in network for network in allowed):
                    raise LabError("IP_DENIED", "DHCP address is outside the configured lab network")
                ip = str(candidate)
        return {"vm_id": vm_id, "image_id": record["image_id"], "ip": ip,
                "state": virsh("domstate", record["domain"]).output.strip(),
                "ram_mib": int(xml.findtext("memory")) // 1024,
                "vcpus": int(xml.findtext("vcpu"))}

    def ssh_args(self, record: dict, ip: str, *, scp: bool = False) -> list[str]:
        folder = self.store.job(record["job_id"]) / "vm" / record["vm_id"]
        host_pub = (folder / "host_key.pub").read_text().split()
        known_hosts = folder / "known_hosts"
        known_hosts.write_text(f"{record['vm_id']} {host_pub[0]} {host_pub[1]}\n")
        return ["scp" if scp else "ssh", "-F", "/dev/null", "-i", str(folder / "client_key"),
                "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=5",
                "-o", "ConnectionAttempts=1", "-o", "StrictHostKeyChecking=yes",
                "-o", f"UserKnownHostsFile={known_hosts}", "-o", "GlobalKnownHostsFile=/dev/null",
                "-o", f"HostKeyAlias={record['vm_id']}", "-o", "HostKeyAlgorithms=ssh-ed25519",
                "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2",
                *([] if scp else [f"lab@{ip}"])]

    def wait(self, job_id: str, vm_id: str, timeout: int) -> dict:
        record = self.record(job_id, vm_id)
        fault = self.store.state(job_id).fault
        if fault in VM_FAULTS:
            raise LabError(fault, "Controlled VM fault; no hypervisor changes", simulated=True)
        deadline = time.monotonic() + timeout
        saw_ip = saw_ssh = False
        last_output = ""
        state: dict = {}

        def fail(code: str, message: str):
            evidence = {"job_id": job_id, "vm_id": vm_id, "domain": record["domain"],
                        "timeout_seconds": timeout, "saw_ip": saw_ip, "saw_ssh": saw_ssh,
                        "last_state": state, "last_output": redact(last_output[-8000:])}
            if saw_ssh and state.get("ip"):
                try:
                    result = run(self.ssh_args(record, state["ip"]) + [
                        "cloud-init status --long; "
                        "tail -n 60 /var/log/cloud-init-output.log /var/log/cloud-init.log 2>&1"],
                        timeout=8, limit=64 * 1024)
                    evidence["guest_diagnostics"] = {**result.model_dump(), "output": redact(result.output)}
                except Exception as exc:
                    evidence["diagnostic_error"] = redact(str(exc))
            # Preserve evidence before automatic cleanup removes the guest and its keys.
            try:
                path = self.store.job(job_id) / "logs" / f"{vm_id}-readiness.log"
                path.write_text(json.dumps(evidence, indent=2))
                evidence["log_path"] = str(path)
            except OSError as exc:
                evidence["logging_error"] = str(exc)
            raise LabError(code, message, **evidence)

        while time.monotonic() < deadline:
            state = self.status(job_id, vm_id)
            if state.get("state") in {"shut off", "crashed"}:
                fail("VM_STOPPED", "Guest stopped before readiness; inspect the saved VM state")
            if state["ip"]:
                saw_ip = True
                result = run(self.ssh_args(record, state["ip"]) + [
                    "test -f /var/lib/cloud/instance/lab-ready"],
                    timeout=min(10, max(0.1, deadline - time.monotonic())))
                last_output = result.output
                if any(message in last_output for message in (
                        "REMOTE HOST IDENTIFICATION HAS CHANGED", "Host key verification failed")):
                    fail("SSH_HOST_KEY_MISMATCH", "Pinned SSH host identity check failed; readiness retry is unsafe")
                if result.returncode != 255 and not result.timed_out:
                    saw_ssh = True
                if result.returncode == 0 and not result.timed_out:
                    return {**state, "ssh_ready": True, "cloud_init_ready": True}
            time.sleep(min(2, max(0, deadline - time.monotonic())))
        code = "BOOT_TIMEOUT" if saw_ssh else "SSH_TIMEOUT" if saw_ip else "NO_IP"
        fail(code, "VM readiness deadline expired")

    def _destroy(self, record: dict) -> dict:
        xml = self.domain_xml(record)
        if xml is not None:
            state = virsh("domstate", record["domain"]).output.strip()
            if state != "shut off":
                virsh("destroy", record["domain"])
            virsh("undefine", record["domain"])
        if self.domain_xml(record) is not None:
            raise LabError("CLEANUP_FAILED", "VM domain still exists")
        self.init_storage()
        disk_dir = self.storage / record["vm_id"]
        if disk_dir.is_symlink():
            raise LabError("PATH_DENIED", "VM storage unexpectedly became a symlink")
        if disk_dir.exists():
            shutil.rmtree(disk_dir)
        record["status"], record["destroyed_at"] = "DESTROYED", now()
        self.save_record(record)
        # Preserve diagnostic XML and evidence, remove all per-VM private material.
        folder = self.store.job(record["job_id"]) / "vm" / record["vm_id"]
        for name in ("client_key", "host_key", "user-data"):
            (folder / name).unlink(missing_ok=True)
        state = self.store.state(record["job_id"])
        if state.current_vm_id == record["vm_id"]:
            state.current_vm_id = None
            self.store.save(state)
        return {"vm_id": record["vm_id"], "verified_absent": True, "base_preserved": True}

    def destroy(self, job_id: str, vm_id: str) -> dict:
        with self.store.lock("vm-global"):
            return self._destroy(self.record(job_id, vm_id))

    def reap(self) -> dict:
        removed = []
        for record in self.records():
            if record["status"] == "DESTROYED":
                continue
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(record["created_at"])).total_seconds()
            if age >= MAX_LIFETIME or record["status"] == "FAILED":
                with self.store.lock(record["job_id"]):
                    removed.append(self.destroy(record["job_id"], record["vm_id"]))
        return {"removed": removed}


class TestBroker:
    __test__ = False

    def __init__(self, store: Store):
        self.store, self.vm = store, VMBroker(store)

    def run(self, job_id: str, vm_id: str, image_id: str, suite: str) -> dict:
        if suite != "smoke":
            raise LabError("SUITE_DENIED", "Only the smoke guest suite is allowed")
        record = self.vm.record(job_id, vm_id)
        if record["image_id"] != image_id:
            raise LabError("WRONG_IMAGE_BINDING", "VM was created for a different image")
        image = self.vm.image(job_id, image_id)
        ready = self.vm.wait(job_id, vm_id, timeout=240)
        test_id = new_id("TEST")
        job = self.store.job(job_id)
        artifact = job / "builds" / f"{image['artifact_id']}.whl"
        if sha256(read_file(job / "builds", artifact.name)) != image["artifact_sha256"]:
            raise LabError("ARTIFACT_MISMATCH", "Artifact changed before guest transfer")
        evidence = {"test_id": test_id, "job_id": job_id, "vm_id": vm_id, "image_id": image_id,
                    "artifact_sha256": image["artifact_sha256"], "suite": suite, "started_at": now(),
                    "status": "FAIL", "simulated": False}
        try:
            checked(self.vm.ssh_args(record, ready["ip"], scp=True) + [str(artifact),
                    f"lab@{ready['ip']}:/home/lab/artifact.whl"], timeout=60)
            expected = json.dumps({"job_id": job_id, "vm_id": vm_id, "image_id": image_id,
                                   "artifact_sha256": image["artifact_sha256"]})
            script = f'''import hashlib, json, pathlib, sys, tempfile
expected = json.loads({expected!r})
assert json.loads(pathlib.Path('/etc/lab-binding.json').read_text()) == expected
artifact = pathlib.Path('/home/lab/artifact.whl')
assert hashlib.sha256(artifact.read_bytes()).hexdigest() == expected['artifact_sha256']
assert pathlib.Path('/var/lib/cloud/instance/lab-ready').exists()
with tempfile.TemporaryFile(dir='/home/lab') as f:
    f.write(b'guest-write'); f.flush()
sys.path.insert(0, str(artifact))
from toy_lab import add
assert add(2, 3) == 5
print(json.dumps(dict(status='PASS', python=sys.version.split()[0], **expected)))
'''
            result = checked(self.vm.ssh_args(record, ready["ip"]) + ["python3 -"],
                             input_text=script, timeout=60, separate_stderr=True)
            output = json.loads(result.output)
            if output.get("status") != "PASS":
                raise LabError("GUEST_TEST_FAILED", "Guest did not return passing evidence")
            evidence.update(status="PASS", duration=result.duration, guest=output,
                            log_id=f"{test_id}.log")
            (job / "logs" / f"{test_id}.log").write_text(result.output)
            state = self.store.state(job_id)
            state.status = "COMPLETE"
            self.store.save(state)
            return evidence
        except LabError as exc:
            evidence.update(code=exc.code, message=exc.message)
            (job / "logs" / f"{test_id}.log").write_text(json.dumps(exc.data))
            raise
        finally:
            atomic_json(job / "tests" / f"{test_id}.json", evidence)
