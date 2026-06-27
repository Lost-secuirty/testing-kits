# Changelog

All notable changes to `testing-kits` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Counts below describe a documented snapshot (see [`docs/GOLDEN_STATS.md`](./docs/GOLDEN_STATS.md));
re-run `make proof` before treating them as a fresh release claim. This repository is a
library of inspectable test harnesses, not a deployed application, and it does not claim total
correctness for any target software.

## [Unreleased]

Slated for the first tagged public release (`1.0.0`). No changes are pending merge; this
section consolidates the current shape of the library for that release. On release, this
heading becomes `## [1.0.0] - <date>` and the version is bumped in `pyproject.toml`.

### Added — harness collection

- 100 pure-standard-library test harnesses across `core` (56), `security` (20), `ai` (16),
  and `pharmacy` (8), with zero runtime dependencies for the collection. Each harness pairs a
  known-good case with a planted-bad case and ships a paired `unittest`; most expose a
  `--self-test` / `--json` / `--list-scenarios` CLI.
- TEETH proof model — 92 harnesses are `required`: they declare a module-level `TEETH`
  contract, and the gate verifies the correct oracle is not flagged while every planted mutant
  is caught. 8 `pharmacy` harnesses remain `legacy` under the older soft gate. Documented
  snapshot (Batch 11, 2026-06-27): **92 required / 0 pending / 8 legacy / 0 failing**.
- OWASP 2025 coverage, mapped by `tools/owasp_coverage.py`: web Top 10 `A01`–`A10` and LLM
  Top 10 `LLM01`, `LLM02`, and `LLM04`–`LLM10`.
- Exploratory Proof Layer (Batch 11): `core/combinatorial_coverage`,
  `core/counterexample_replay`, `core/stateful_sequence_budget`, and
  `core/boundary_corpus_expander`.
- Deferred-OWASP closeout (Batch 11): `security/data_integrity` (A08),
  `ai/data_poisoning` (LLM04), `ai/system_prompt_leakage` (LLM07), and
  `ai/misinformation` (LLM09).

### Added — proof, meta-gates, and tooling

- Anti-overclaim gate suite that keeps the proof claims honest: `proof`, `vacuity`, `purity`,
  `circularity`, `corpus_size`, `fragility`, `dead_expr`, a protected-file guard, and a
  gate-canary meta-check that confirms the gates still bite.
- `tools/proof_audit.py` (proof ratchet), `tools/owasp_coverage.py` (coverage matrix +
  registry/tree sync check), and `tools/findings_export.py` (SARIF 2.1.0 and flat-JSON export).
- Generated harness cards under `cards/`, with a teeth ratchet pinning all 100 harnesses so a
  proof-strength regression fails CI.

### Added — supply chain and CI

- Release workflow on `v*` tags that produces an SPDX SBOM, `SHA256SUMS`, a build-provenance
  attestation, and a GitHub Release.
- Security and quality automation: CodeQL, OpenSSF Scorecard, gitleaks plus a custom
  staged-diff secret/PII scan, OSV-Scanner / dependency review, workflow-and-shell lint, an
  instruction/control audit, and Dependabot. GitHub Actions are pinned by commit SHA.
- CI test matrix across Python 3.10–3.14, plus a pre-commit gate suite.

### Governance

- MIT `LICENSE`, `SECURITY.md`, a pull-request template, a deep `docs/` set, and `llms.txt`.

<!-- On the 1.0.0 release this becomes a compare link (v1.0.0...HEAD) plus a
     [1.0.0] release-tag link. -->
[Unreleased]: https://github.com/Lost-secuirty/testing-kits/commits/main
