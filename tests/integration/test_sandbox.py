import pytest

from agentlab.models import LabError
from agentlab.sandbox import Sandbox
from agentlab.validate import Validation

pytestmark = pytest.mark.integration


def test_real_sandbox_workflow(store):
    validation = Validation(store, "sandbox")
    try:
        assert validation.sandbox_workflow() is not None
        assert not any(row["status"] == "FAIL" for row in validation.rows)
    finally:
        validation.cleanup()


def test_real_timeout_stops_container(store, job):
    sandbox = Sandbox(store)
    sandbox.create(job["job_id"])
    try:
        with pytest.raises(LabError) as exc:
            sandbox.execute(job["job_id"], ["python3", "-c", "import time; time.sleep(15)"], timeout=1)
        assert exc.value.code == "BUILD_TIMEOUT"
        assert not sandbox.inspect(job["job_id"])["State"]["Running"]
    finally:
        sandbox.destroy(job["job_id"])
