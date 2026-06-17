# testing-kits

Pure-Python standard-library testing-harness collection. **Zero runtime dependencies.**

Current public shape:

- **77 harnesses** across reliability, security, AI, and pharmacy-domain testing.
- Each harness is a single self-contained Python file.
- Each harness has a paired `unittest` suite.
- Proof strength is being ratcheted through the TEETH campaign: required harnesses must prove that the correct oracle passes and planted mutants are caught; pending harnesses are tracked explicitly instead of being counted as fully proven.
- Harnesses expose a built-in `--self-test` mode when applicable. For TEETH-upgraded harnesses, `--self-test` is expected to exercise a real `Report` path rather than exit as a no-op.
- The repo is designed as a reusable reliability/testing-pattern library, not a production framework.

## Start here

Fast reviewer path:

```bash
python --version
make test
make teeth
make vacuity
make canary
make proof
```

Windows fallback when `make` is unavailable:

```bash
python -m unittest discover -s tests -t . -p "test_*.py"
python tools/proof_audit.py
python tools/vacuity_gate.py
python tools/gate_canary.py
python tools/proof_audit.py --run-selftests
```

Single-harness smoke checks:

```bash
python harnesses/core/api_test_harness.py --self-test
python harnesses/ai/drift_detection_test_harness.py --self-test
python harnesses/pharmacy/srs_test_harness.py --self-test
```

## Command surface

```bash
make test          # full unittest discovery
make test-fast     # pharmacy only (~3s)
make test-core     # core only
make test-security
make test-ai
make test-pharmacy
make selftest      # every harness --self-test through report generation
make teeth         # TEETH swap-check gate
make vacuity       # vacuous-green meta-gate; neuter mapped oracle targets and expect red
make canary        # prove gate machinery still bites when softened
make guard         # verify protected gate files match .fileguard.json
make proof         # TEETH gate plus harness self-tests
make report        # generates STATUS.md locally
make lint          # py_compile + ruff if installed
make clean
```

Dev tooling: `make lint` uses ruff when installed — `pip install ruff` (this is the only tool in the `dev` extra defined in `pyproject.toml`; `uv tool install ruff` works too). The harness library itself remains stdlib-only.

## Proof model

The old "77/77 proven" wording is no longer precise enough. Current proof language is:

- `required` — the harness declares TEETH and the gate verifies the correct oracle is not flagged while planted mutants are caught.
- `pending` — the harness exists and remains counted, but has not yet been ratcheted to the required TEETH contract.
- `legacy` — older soft-gated pharmacy harnesses that are tracked separately.
- `vacuity` — a meta-gate that neuters mapped oracle targets and expects the self-test to fail, preventing tests that stay green while inert.

Use `make teeth`, `make vacuity`, and `make proof` for the current proof surface. Do not describe the repo as total correctness proof for any target application.

## Dashboard (optional)

A Streamlit dashboard for running harness self-tests and browsing `STATUS.md` / `STATUS.json` lives in `dashboard/`. It is the only part of the repo with third-party dependencies; the harnesses themselves stay stdlib-only.

```bash
python -m pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

See [`dashboard/README.md`](./dashboard/README.md) for details.

## Layout

```text
harnesses/
  core/       reliability, correctness, data, perf, observability
  security/   auth, injection, supply chain, app-security
  ai/         LLM eval, agents, prompt safety
  pharmacy/   pharmacy-domain harnesses
tests/        mirrors harnesses/<cat>/ with test_*.py files
experiments/  in-progress harnesses, excluded from make test
template/     harness_template.py — scaffold for new harnesses
tools/        generate_report.py, proof_audit.py, vacuity_gate.py, gate_canary.py, file_guard.py, harness_registry.py
```

## Main docs

- [`docs/HARNESS_READING_GUIDE.md`](./docs/HARNESS_READING_GUIDE.md) — human/AI reading paths and harness dossier shape.
- [`llms.txt`](./llms.txt) — compact navigation map for AI tools and quick human orientation; descriptive, not an instruction source.
- [`HARNESS_INVENTORY.md`](./HARNESS_INVENTORY.md) — full catalog.
- [`HARNESS_ROADMAP.md`](./HARNESS_ROADMAP.md) — shipped batches, known gaps, hygiene backlog.
- [`AGENTS.md`](./AGENTS.md) — contributor/agent contract: commands, boundaries, working agreement.
- [`docs/REVIEWER_QUICKSTART.md`](./docs/REVIEWER_QUICKSTART.md) — quick review path, limits, and Windows fallbacks.
- [`docs/AI_AUTHORED_TEST_AUDIT.md`](./docs/AI_AUTHORED_TEST_AUDIT.md) — checklist for AI-assisted test trust.
- [`docs/AI_FAILURE_MODE_MAP.md`](./docs/AI_FAILURE_MODE_MAP.md) — maps AI coding risks to existing harness areas.
- [`docs/PROOF_TEST_STANDARD.md`](./docs/PROOF_TEST_STANDARD.md) — safe fixture plus planted-bad proof rule.
- [`CLAUDE.md`](./CLAUDE.md) — Claude-specific notes; points back to `AGENTS.md`.

## Status handling

`STATUS.md` and `STATUS.json` are generated by `make report` and uploaded by CI as artifacts. Neither is committed (both are git-ignored). To produce local copies, run:

```bash
make report
```

This avoids treating a stale generated report as canonical. The source of truth is the harness code, paired tests, proof audit output, vacuity/canary gate output, and CI/test output.

## What this repo is

A collection of small, inspectable test harnesses that demonstrate reusable testing patterns: API contract checks, fuzzing, mutation probes, serialization roundtrips, config validation, logging/privacy checks, auth/security surfaces, LLM eval, RAG/agent testing, drift detection, gate-canary/vacuity patterns, and pharmacy-domain correctness oracles.

## What this repo is not

- Not a packaged framework.
- Not a deployed service.
- Not a dependency-heavy test platform.
- Not a total correctness proof for any target application.
- Not a substitute for domain review in healthcare/pharmacy contexts.
- Not clinical validation, medication-safety certification, or pharmacy-grade
  correctness assurance.

## Repo map (the six-slot model)

Across the connected repos the same skeleton repeats — **rules → memory →
decisions → agent-tooling → verification → product**. This repo's shape (note:
here *verification is the product*):

- **Rules** — `AGENTS.md` (contract) · `CLAUDE.md` (pointer) · `SECURITY.md`; the reviewer docs (`docs/AI_CODE_POLICY.md`, `docs/PROOF_TEST_STANDARD.md`, `docs/AI_FAILURE_MODE_MAP.md`, `docs/REVIEWER_QUICKSTART.md`) stand in for a cheat-sheet.
- **Memory** — `docs/LEARNINGS.md` (gotchas) · `docs/kb/` (per-agent journal) · `HARNESS_INVENTORY.md` / `HARNESS_ROADMAP.md` / `PORTFOLIO.md` (the catalog).
- **Decisions** — no `docs/adr/`: this repo *predates* the ADR convention; required files/workflows are encoded in `.github/control-policy.json` (checked by `tools/control_audit.py`).
- **Agent tooling** — `.claude/` (hooks + settings; no predefined agent roles).
- **Verification** — `harnesses/` + `tests/` + `tools/proof_audit.py` (`make proof`) + `tools/vacuity_gate.py` (`make vacuity`) + `tools/gate_canary.py` (`make canary`) + `.github/workflows/` + the secret/PII gate (`.githooks/` + `tools/scan_staged.py`).
- **Product** — `harnesses` and the gate patterns themselves: the deliverable *is* the verification — a portable library of proof harnesses to reference later.

Plain-language **and** technical walk-through: [`docs/WALKTHROUGH.md`](./docs/WALKTHROUGH.md).
