import sys
import time

import pytest

from agentlab.models import LabError
from agentlab.process import checked, run


def test_timeout_stops_process_group():
    start = time.monotonic()
    result = run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.15)
    assert result.timed_out
    assert time.monotonic() - start < 2


def test_unbounded_stdout_is_limited():
    result = run([sys.executable, "-c", "while True: print('x'*1000, flush=True)"], limit=4096)
    assert result.output_limited and len(result.output) == 4096


def test_command_string_is_never_interpreted_by_shell(tmp_path):
    target = tmp_path / "should-not-exist"
    data = f"$(touch {target})"
    result = checked([sys.executable, "-c", "import sys; print(sys.argv[1])", data])
    assert data in result.output
    assert not target.exists()


def test_failed_command_is_not_success():
    with pytest.raises(LabError) as exc:
        checked([sys.executable, "-c", "raise SystemExit(7)"])
    assert exc.value.code == "COMMAND_FAILED"


def test_credentials_not_inherited(monkeypatch):
    monkeypatch.setenv("PRIVATE_TEST_TOKEN", "do-not-inherit")
    result = checked([sys.executable, "-c", "import os; assert 'PRIVATE_TEST_TOKEN' not in os.environ"])
    assert result.returncode == 0


def test_structured_stdout_not_polluted_by_diagnostics():
    result = checked([sys.executable, "-c", "import sys; print('<xml/>'); print('warning', file=sys.stderr)"],
                     separate_stderr=True)
    assert result.output == "<xml/>\n" and result.stderr == "warning\n"


def test_oversized_stdin_rejected_before_launch():
    with pytest.raises(LabError) as exc:
        run(["not-a-real-command"], input_text="x" * 5000)
    assert exc.value.code == "INPUT_LIMIT"
