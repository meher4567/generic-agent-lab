import json

from agentlab.validate import Validation


def test_partial_profile_never_claims_environment_ready(store):
    validation = Validation(store, "software")
    validation.rows = [{"name": "software", "status": "PASS", "evidence": "ok"}]
    report = validation.write_report(finished=True)
    assert report["status"] == "PARTIAL" and report["environment_ready"] is False


def test_skipped_full_check_cannot_pass(store):
    validation = Validation(store, "full")
    validation.rows = [{"name": "vm.boot", "status": "SKIP", "evidence": "no KVM"}]
    report = validation.write_report(finished=True)
    assert report["status"] != "PASS" and report["environment_ready"] is False


def test_failure_persists_and_html_escapes_evidence(store):
    validation = Validation(store, "full")
    validation.rows = [{"name": "test", "status": "FAIL", "evidence": "<script>alert(1)</script>"}]
    report = validation.write_report(finished=True)
    assert report["status"] == "FAIL"
    assert "<script>" not in (validation.folder / "report.html").read_text()
    assert json.loads((validation.folder / "report.json").read_text())["environment_ready"] is False


def test_small_vm_count_is_explicitly_partial(store):
    validation = Validation(store, "full", vm_count=1)
    validation.rows = [{"name": "vm", "status": "PASS", "evidence": "ok"}]
    assert validation.write_report(finished=True)["environment_ready"] is False
