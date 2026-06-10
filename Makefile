.PHONY: test test-fast test-core test-security test-ai test-pharmacy selftest proof report lint clean help

PY ?= python3

help:
	@echo "Targets:"
	@echo "  test           Run full unittest discovery"
	@echo "  test-fast      Run pharmacy/ tests only (~3s)"
	@echo "  test-core      Run core/ tests only"
	@echo "  test-security  Run security/ tests only"
	@echo "  test-ai        Run ai/ tests only"
	@echo "  test-pharmacy  Same as test-fast"
	@echo "  selftest       Run --self-test for every harness"
	@echo "  proof          Run proof audit plus harness self-tests"
	@echo "  report         Regenerate STATUS.md"
	@echo "  lint           py_compile + ruff if installed"
	@echo "  clean          Remove __pycache__ and *.pyc"

test:
	$(PY) -m unittest discover -s tests -t . -p "test_*.py"

test-fast: test-pharmacy

test-core test-security test-ai test-pharmacy:
	$(PY) -m unittest discover -s tests/$(@:test-%=%) -t . -p "test_*.py"

selftest:
	$(PY) tools/generate_report.py --check

proof:
	$(PY) tools/proof_audit.py --run-selftests

report:
	$(PY) tools/generate_report.py

lint:
	$(PY) -m compileall -q harnesses tests tools
	@command -v ruff >/dev/null && ruff check harnesses tests tools || echo "ruff not installed; skipping ruff check"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
