import pytest

from agentlab.store import Store
from agentlab.validate import Validation


@pytest.mark.kvm
def test_full_environment():
    # Only explicitly enabled on a provisioned host; full validation cleans up its jobs.
    report, code = Validation(Store(), "full", vm_count=3).run()
    assert code == 0 and report["environment_ready"]
