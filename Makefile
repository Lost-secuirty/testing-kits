.PHONY: test test-fast test-core test-security test-ai test-pharmacy selftest teeth proof canary mutmut report lint clean help

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
	@echo "  teeth          Run the TEETH swap-check gate (cross-platform, mandatory)"
	@echo "  proof          Run proof audit (teeth + self-tests)"
	@echo "  canary         Prove every anti-bug gate still bites (gate-canary)"
	@echo "  mutmut         Advisory mutation lane (Linux/WSL only; never blocks)"
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

teeth:
	$(PY) tools/proof_audit.py

proof:
	$(PY) tools/proof_audit.py --run-selftests

canary:
	$(PY) tools/gate_canary.py

mutmut:
	$(PY) tools/mutmut_lane.py

report:
	$(PY) tools/generate_report.py

lint:
	$(PY) -m compileall -q harnesses tests tools
	@if command -v uv >/dev/null 2>&1; then \
		uv run ruff check harnesses tests tools; \
	elif command -v ruff >/dev/null 2>&1; then \
		ruff check harnesses tests tools; \
	else \
		echo "ruff not installed; skipping ruff check"; \
	fi

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
