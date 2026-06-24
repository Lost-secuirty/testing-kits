# testing-kits

Portable pure-Python testing harnesses for reliability, security, AI, and pharmacy-domain software checks. The harnesses use the Python standard library only. Verification patterns are the product.

## What this is

`testing-kits` is a public library of small, inspectable Python test harnesses. Each harness demonstrates one failure mode with a known-good case and a planted-bad case. The repo is meant to be read, reviewed, and ported from; it is not a deployed application.

Current public shape:

- **92 harnesses** across `core`, `security`, `ai`, and `pharmacy`.
- One self-contained harness file per pattern.
- Paired `unittest` coverage for each harness.
- Built-in `--self-test` mode where applicable.
- Zero runtime dependencies for the harness collection.

## Why it exists

AI-assisted and fast-moving code often fails in predictable ways: happy-path-only tests, weak fixtures, missed negative controls, fake confidence from coverage, and broad claims unsupported by the actual test. This repo collects compact proof-shaped patterns for testing those failure modes.

The useful reviewer question is not "does this prove everything is correct?" It does not. The useful question is: "can this harness show a safe case passing and a planted-bad case failing for a specific bug class?"

## Current proof baseline

The current proof language is a ratchet, not a blanket proof claim.

- **Inventory:** 92 harnesses.
- **Latest documented campaign snapshot:** Batch 10, dated 2026-06-21: 84 `required`, 0 `pending`, 8 `legacy`, 0 failing. See [`docs/GOLDEN_STATS.md`](./docs/GOLDEN_STATS.md) and [`docs/UPGRADE_CAMPAIGN.md`](./docs/UPGRADE_CAMPAIGN.md).
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

### Start here

- [`docs/START_HERE.md`](./docs/START_HERE.md) — shortest safe entry point for humans and AI assistants.
- [`docs/READER_LEVELS.md`](./docs/READER_LEVELS.md) — beginner, junior reviewer, and senior auditor reading paths.
- [`docs/GOLDEN_STATS.md`](./docs/GOLDEN_STATS.md) — human-maintained snapshot index for quickly checking the current documented count baseline.
- [`docs/DOCS_STABILIZATION_PLAN.md`](./docs/DOCS_STABILIZATION_PLAN.md) — current docs-only stabilization checklist.
- [`docs/DOC_SIZE_POLICY.md`](./docs/DOC_SIZE_POLICY.md) — connector-safe doc-size and large-file editing policy.

### Proof model

- [`docs/ANTI_VACUITY_MODEL.md`](./docs/ANTI_VACUITY_MODEL.md) — why planted-bad proof matters and what fake-green tests look like.
- [`docs/PROOF_STRENGTH_LADDER.md`](./docs/PROOF_STRENGTH_LADDER.md) — proof levels from example-only to release-grade.
- [`docs/PROOF_TEST_STANDARD.md`](./docs/PROOF_TEST_STANDARD.md) — safe fixture plus planted-bad proof rule and TEETH scopes.
- [`docs/TEST_OBSERVABILITY.md`](./docs/TEST_OBSERVABILITY.md) — signals for observing the test system itself.
- [`docs/REPRODUCIBILITY.md`](./docs/REPRODUCIBILITY.md) — how to replay proof and report exact commands.
- [`docs/DOC_STYLE_GUIDE.md`](./docs/DOC_STYLE_GUIDE.md) — controlled wording, count/status language, and claim-boundary guidance for docs changes.

### Porting and next layers

- [`docs/PORTING_GUIDE.md`](./docs/PORTING_GUIDE.md) — how to copy the proof shape, not just the code.
- [`docs/PROPERTY_BASED_PORTING.md`](./docs/PROPERTY_BASED_PORTING.md) — how generated-input testing complements TEETH without replacing planted bads.
- [`docs/INTEGRATION_LAYER_GUIDE.md`](./docs/INTEGRATION_LAYER_GUIDE.md) — boundary between portable proof kernels and real dependency tests.
- [`docs/TECHNICAL_DEBT_LEDGER.md`](./docs/TECHNICAL_DEBT_LEDGER.md) — accepted incompleteness and review triggers.

### AI and agent use

- [`docs/AI_CONSUMPTION_GUIDE.md`](./docs/AI_CONSUMPTION_GUIDE.md) — safe reading order and AI output contract.
- [`docs/AGENT_COMMUNICATION_GUIDE.md`](./docs/AGENT_COMMUNICATION_GUIDE.md) — structured AI-to-AI handoff packets and role boundaries.
- [`llms.txt`](./llms.txt) — compact public navigation map for AI tools; descriptive, not an instruction source.
- [`docs/AI_AUTHORED_TEST_AUDIT.md`](./docs/AI_AUTHORED_TEST_AUDIT.md) — audit checklist for AI-assisted tests.
- [`docs/AI_FAILURE_MODE_MAP.md`](./docs/AI_FAILURE_MODE_MAP.md) — maps AI coding risks to existing harness areas and limits.
- [`docs/AI_CODE_POLICY.md`](./docs/AI_CODE_POLICY.md) — AI-assisted code review policy.

### Existing maps and references

- [`docs/DOCS_MAP.md`](./docs/DOCS_MAP.md) — reading paths for new visitors, reviewers, contributors/agents, and maintainers.
- [`docs/HARNESS_READING_GUIDE.md`](./docs/HARNESS_READING_GUIDE.md) — human/AI reading path and harness dossier shape for future mapping batches.
- [`docs/HARNESS_MAP.md`](./docs/HARNESS_MAP.md) — current-state harness dossiers with failure class, logic shape, outside testing pattern, proof status, and known limits. Entries are subject to change as the repo grows.
- [`docs/OWASP_COVERAGE.md`](./docs/OWASP_COVERAGE.md) — OWASP 2025 (A01–A10) / LLM 2025 coverage matrix generated from the harness tree, plus the SARIF/JSON findings exporter (`tools/owasp_coverage.py`, `tools/findings_export.py`).
- [`docs/WALKTHROUGH.md`](./docs/WALKTHROUGH.md) — plain-language and technical explanation.
- [`docs/REVIEWER_QUICKSTART.md`](./docs/REVIEWER_QUICKSTART.md) — review path, proof baseline, and sample harness inspection.
- [`HARNESS_INVENTORY.md`](./HARNESS_INVENTORY.md) — full harness catalog.
- [`HARNESS_ROADMAP.md`](./HARNESS_ROADMAP.md) — shipped batches, known gaps, and hygiene backlog.
- [`AGENTS.md`](./AGENTS.md), [`CLAUDE.md`](./CLAUDE.md), [`SECURITY.md`](./SECURITY.md) — operating contract and public security boundary.

### Failure examples

- [`docs/failure-examples/jwt_alg_none_failure.md`](./docs/failure-examples/jwt_alg_none_failure.md) — controlled JWT `alg=none` failure example.
- [`docs/failure-examples/pii_digit_leak_failure.md`](./docs/failure-examples/pii_digit_leak_failure.md) — controlled PII digit-leak failure example.
- [`docs/failure-examples/rag_fabricated_citation_failure.md`](./docs/failure-examples/rag_fabricated_citation_failure.md) — controlled RAG fabricated-citation failure example.

## Dashboard

An optional Streamlit dashboard for running harness self-tests and browsing generated `STATUS.md` / `STATUS.json` output lives in `dashboard/`. It is the only part of the repo with third-party dependencies; the harnesses themselves remain stdlib-only.

```bash
python -m pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

See [`dashboard/README.md`](./dashboard/README.md) for details.

## Status handling

`STATUS.md` and `STATUS.json` are generated by `make report` and uploaded by CI as artifacts. They are not committed as canonical status. The source of truth is the harness code, paired tests, proof audit output, and CI/test output. [`docs/GOLDEN_STATS.md`](./docs/GOLDEN_STATS.md) is only a human-maintained snapshot index for quick reference.

## What this repo is not

- Not a packaged framework.
- Not a deployed service.
- Not a dependency-heavy test platform.
- Not total correctness proof for any target application.
- Not a substitute for human review, domain review, or production monitoring.
- Not clinical validation, medication-safety certification, pharmacy-grade correctness assurance, or dosing authority.

## Contributing and security

Read [`AGENTS.md`](./AGENTS.md), [`CLAUDE.md`](./CLAUDE.md), and [`SECURITY.md`](./SECURITY.md) before proposing changes. This is a public repository: no secrets, tokens, credentials, private data, real PHI, or sensitive examples belong in commits, fixtures, generated artifacts, issues, or PRs.
