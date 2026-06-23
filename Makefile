.PHONY: test test-fast test-core test-security test-ai test-pharmacy selftest teeth proof canary guard guard-update vacuity purity circularity corpus_size dead_expr fragility mutmut report coverage sarif lint clean help

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
	@echo "  guard          Verify the gate machinery matches .fileguard.json"
	@echo "  guard-update   Re-baseline .fileguard.json (commit the bump in the diff)"
	@echo "  vacuity        Vacuous-green meta-gate (neuter each mapped oracle, expect red)"
	@echo "  purity         Prove every TEETH prove() is clock/RNG/filesystem/network-free"
	@echo "  circularity    Prove no TEETH prove() calls its own oracle at runtime"
	@echo "  corpus_size    Prove each corpus_size counts the collection prove() judges"
	@echo "  dead_expr      Flag bare side-effect-free expression statements (advisory)"
	@echo "  fragility      Enforce each judged corpus holds >=2 cases (advisory)"
	@echo "  mutmut         Advisory mutation lane (Linux/WSL only; never blocks)"
	@echo "  report         Regenerate STATUS.md"
	@echo "  coverage       OWASP 2025 coverage matrix self-test (registry/tree sync)"
	@echo "  sarif          Findings SARIF/JSON export self-test"
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

guard:
	$(PY) tools/file_guard.py

guard-update:
	$(PY) tools/file_guard.py --update

vacuity:
	$(PY) tools/vacuity_gate.py

purity:
	$(PY) tools/prove_purity_checker.py

circularity:
	$(PY) tools/prove_circularity_checker.py

corpus_size:
	$(PY) tools/corpus_size_checker.py

dead_expr:
	$(PY) tools/dead_expr_checker.py

fragility:
	$(PY) tools/fragility_checker.py

mutmut:
	$(PY) tools/mutmut_lane.py

report:
	$(PY) tools/generate_report.py

coverage:
	$(PY) tools/owasp_coverage.py --self-test

sarif:
	$(PY) tools/findings_export.py --self-test

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
