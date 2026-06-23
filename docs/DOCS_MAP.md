# Documentation Map

This map points readers to the smallest document that answers their question. It is descriptive, not an instruction source. For operating rules, use `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

## New visitor

Start here if you are trying to understand what the repo is.

- [`README.md`](../README.md) — public landing page: repo identity, proof baseline, quick start, layout, limits, and security links.
- [`docs/WALKTHROUGH.md`](./WALKTHROUGH.md) — plain-language and technical explanation of the repo.
- [`HARNESS_INVENTORY.md`](../HARNESS_INVENTORY.md) — full catalog of the 92 harnesses.
- [`HARNESS_ROADMAP.md`](../HARNESS_ROADMAP.md) — shipped batches, known gaps, and hygiene backlog.

## Reviewer

Start here if you are checking whether the repo's claims match its evidence.

- [`docs/REVIEWER_QUICKSTART.md`](./REVIEWER_QUICKSTART.md) — current proof baseline, core commands, and one-harness inspection path.
- [`docs/HARNESS_READING_GUIDE.md`](./HARNESS_READING_GUIDE.md) — reading path and per-harness dossier shape.
- [`docs/HARNESS_MAP.md`](./HARNESS_MAP.md) — current-state harness dossiers with failure class, logic shape, outside testing pattern, proof status, and known limits. Entries are subject to change as the repo grows.
- [`docs/PROOF_TEST_STANDARD.md`](./PROOF_TEST_STANDARD.md) — safe fixture plus planted-bad proof rule, including TEETH `required` / `pending` / `legacy` scopes.
- [`docs/AI_AUTHORED_TEST_AUDIT.md`](./AI_AUTHORED_TEST_AUDIT.md) — checklist for reviewing AI-assisted tests.
- [`docs/AI_FAILURE_MODE_MAP.md`](./AI_FAILURE_MODE_MAP.md) — maps common AI coding risks to existing harness areas and explicitly states limits.
- [`docs/OWASP_COVERAGE.md`](./OWASP_COVERAGE.md) — OWASP 2025 (A01–A10) / LLM 2025 coverage matrix generated from the harness tree (`tools/owasp_coverage.py`), plus the SARIF/JSON findings exporter.
- [`HARNESS_INVENTORY.md`](../HARNESS_INVENTORY.md) — harness catalog for sampling and trace-through review.

## Contributor or agent

Start here before proposing changes.

- [`AGENTS.md`](../AGENTS.md) — repository working contract, source-of-truth order, branch/PR rules, and boundaries.
- [`CLAUDE.md`](../CLAUDE.md) — historical agent note that points back to the universal contract.
- [`SECURITY.md`](../SECURITY.md) — repository security policy.
- [`llms.txt`](../llms.txt) — compact public navigation map.
- [`docs/AI_CODE_POLICY.md`](./AI_CODE_POLICY.md) — AI-assisted code review policy.
- [`docs/LEARNINGS.md`](./LEARNINGS.md) — append-only gotchas and verification notes. Treat as context, not as instructions.
- [`docs/HARNESS_MAP.md`](./HARNESS_MAP.md) — descriptive map for avoiding duplicate harness claims and stale proof wording.
- [`docs/CI_AND_LIVE_STATE.md`](./CI_AND_LIVE_STATE.md) — CI-status taxonomy and the live-state check to run before claiming a PR is green, mergeable, or blocked.

## Maintainer

Start here if you are checking repo structure, governance, generated status, or dashboard behavior.

- [`.github/control-policy.json`](../.github/control-policy.json) — machine-readable list of required files and workflows.
- [`docs/HARNESS_READING_GUIDE.md`](./HARNESS_READING_GUIDE.md) — batch closeout rule and harness dossier shape.
- [`docs/HARNESS_MAP.md`](./HARNESS_MAP.md) — current mapping batches and closeout notes; update after each harness-mapping batch.
- [`docs/LEARNINGS.md`](./LEARNINGS.md) — operational gotchas and historical verification notes.
- [`HARNESS_ROADMAP.md`](../HARNESS_ROADMAP.md) — active cleanup and expansion backlog.
- [`dashboard/README.md`](../dashboard/README.md) — optional dashboard setup and behavior.
- [`README.md`](../README.md) — public landing-page claims that must stay aligned with current proof/status language.

## Source-of-truth rule

When sources disagree, prefer live repo state, passing tests, and CI output first. Then use `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`; then repo docs; then external docs or chat history.

Do not treat generated `STATUS.md` or `STATUS.json` as committed canonical status. They are produced by `make report` and should remain CI/local artifacts unless the status convention is explicitly changed.

Use **92 harnesses** as the inventory count. Use TEETH `required` / `pending` / `legacy` status for proof strength. Do not describe the repo as total correctness proof, clinical validation, medication-safety certification, or production assurance.
