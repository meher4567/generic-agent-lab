.PHONY: setup test lint smoke doctor clean
setup:
	./scripts/setup-python.sh
test:
	.venv/bin/pytest -q tests/unit tests/fault
lint:
	.venv/bin/ruff check agentlab containers scripts tests
	shellcheck scripts/*.sh
smoke:
	./scripts/smoke-test.sh
doctor:
	./scripts/verify-host.sh
clean:
	./scripts/cleanup.sh
