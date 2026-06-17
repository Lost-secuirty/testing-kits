# testing-kits

Portable pure-Python testing harnesses for reliability, security, AI, and pharmacy-domain software checks. The harnesses use the Python standard library only. Verification patterns are the product.

## What this is

`testing-kits` is a public library of small, inspectable Python test harnesses. Each harness demonstrates one failure mode with a known-good case and a planted-bad case. The repo is meant to be read, reviewed, and ported from; it is not a deployed application.

Current public shape:

- **77 harnesses** across `core`, `security`, `ai`, and `pharmacy`.
- One self-contained harness file per pattern.
- Paired `unittest` coverage for each harness.
- Built-in `--self-test` mode where applicable.
- Zero runtime dependencies for the harness collection.

## Why it exists

AI-assisted and fast-moving code often fails in predictable ways: happy-path-only tests, weak fixtures, missed negative controls, fake confidence from coverage, and broad claims unsupported by the actual test. This repo collects compact patterns for testing those failure modes.

The useful reviewer question is not "does this prove everything is correct?" It does not. The useful question is: "can this harness show a safe case passing and a planted-bad case failing for a specific bug class?"

## Current proof baseline

The current proof language is a ratchet, not a blanket proof claim.

- **Inventory:** 77 harnesses.
- **Loaded Batch 5 proof snapshot:** 51 `required`, 18 `pending`, 8 `legacy`, 0 failing.
- **required:** the harness declares `TEETH`; the gate verifies the correct oracle is not flagged and planted mutants are caught.
- **pending:** the harness is counted but has not yet been ratcheted into the required TEETH contract.
- **legacy:** pharmacy-domain harnesses tracked under the older soft gate.

Re-run `make proof` before treating the proof snapshot as a fresh release claim. Do not describe this repo as total correctness proof for any target application.

## Quick start

```bash
python --version
make test
make selftest
make proof
```

Windows fallback when `make` is unavailable:

```bash
python -m unittest discover -s tests -t . -p "test_*.py"
python tools/generate_report.py --check
python tools/proof_audit.py --run-selftests
```

## Inspect one harness

Start with one traceable example before trusting the inventory.

```bash
python harnesses/core/statistical_rng_oracle_test_harness.py --self-test
python -m unittest tests.core.test_statistical_rng_oracle_test_harness tests.core.test_statistical_rng_oracle_proof
```

Reviewer trace:

1. Open the harness file under `harnesses/<category>/`.
2. Find the known-good fixture or reference implementation.
3. Find the planted-bad fixture, mutant, or negative control.
4. Open the paired test under `tests/<category>/`.
5. Confirm the documentation claims only what the fixture proves.

## Layout

```text
harnesses/
  core/       reliability, correctness, data, perf, observability
  security/   auth, injection, supply chain, app-security
  ai/         LLM eval, agents, prompt safety
  pharmacy/   pharmacy-domain software fixtures
tests/        mirrors harnesses/<category>/ with test_*.py files
experiments/  in-progress harnesses, excluded from make test
template/     harness_template.py — scaffold for new harnesses
tools/        report, proof, registry, scan, and control-audit utilities
dashboard/    optional Streamlit viewer; separate dependency surface
```

## Documentation map

- [`docs/DOCS_MAP.md`](./docs/DOCS_MAP.md) — reading paths for new visitors, reviewers, contributors/agents, and maintainers.
- [`docs/WALKTHROUGH.md`](./docs/WALKTHROUGH.md) — plain-language and technical explanation.
- [`docs/REVIEWER_QUICKSTART.md`](./docs/REVIEWER_QUICKSTART.md) — review path, proof baseline, and sample harness inspection.
- [`HARNESS_INVENTORY.md`](./HARNESS_INVENTORY.md) — full harness catalog.
- [`HARNESS_ROADMAP.md`](./HARNESS_ROADMAP.md) — shipped batches, known gaps, and hygiene backlog.
- [`docs/PROOF_TEST_STANDARD.md`](./docs/PROOF_TEST_STANDARD.md) — safe fixture plus planted-bad proof rule and TEETH scopes.
- [`docs/AI_AUTHORED_TEST_AUDIT.md`](./docs/AI_AUTHORED_TEST_AUDIT.md) — audit checklist for AI-assisted tests.
- [`docs/AI_FAILURE_MODE_MAP.md`](./docs/AI_FAILURE_MODE_MAP.md) — maps AI coding risks to existing harness areas and limits.
- [`docs/AI_CODE_POLICY.md`](./docs/AI_CODE_POLICY.md) — AI-assisted code review policy.
- [`AGENTS.md`](./AGENTS.md), [`CLAUDE.md`](./CLAUDE.md), [`SECURITY.md`](./SECURITY.md) — operating contract and public security boundary.

## Dashboard

An optional Streamlit dashboard for running harness self-tests and browsing generated `STATUS.md` / `STATUS.json` output lives in `dashboard/`. It is the only part of the repo with third-party dependencies; the harnesses themselves remain stdlib-only.

```bash
python -m pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

See [`dashboard/README.md`](./dashboard/README.md) for details.

## Status handling

`STATUS.md` and `STATUS.json` are generated by `make report` and uploaded by CI as artifacts. They are not committed as canonical status. The source of truth is the harness code, paired tests, proof audit output, and CI/test output.

## What this repo is not

- Not a packaged framework.
- Not a deployed service.
- Not a dependency-heavy test platform.
- Not total correctness proof for any target application.
- Not a substitute for human review, domain review, or production monitoring.
- Not clinical validation, medication-safety certification, pharmacy-grade correctness assurance, or dosing authority.

## Contributing and security

Read [`AGENTS.md`](./AGENTS.md), [`CLAUDE.md`](./CLAUDE.md), and [`SECURITY.md`](./SECURITY.md) before proposing changes. This is a public repository: no secrets, tokens, credentials, private data, real PHI, or sensitive examples belong in commits, fixtures, generated artifacts, issues, or PRs.
