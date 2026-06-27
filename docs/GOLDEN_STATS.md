# Golden stats

This file is a human-maintained snapshot index, not executable proof.

The source of truth remains:

- harness code;
- paired unittest suites;
- `tools/proof_audit.py` output;
- `cards/teeth_ratchet.json`, if present;
- CI/test output;
- generated `STATUS.md` / `STATUS.json` artifacts.

## Current snapshot

| Date | Source | Total | Required | Pending | Legacy | Failing | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| 2026-06-18 | `docs/UPGRADE_CAMPAIGN.md` Batch 9 | 77 | 69 | 0 | 8 | 0 | TEETH campaign complete for non-pharmacy harnesses. |
| 2026-06-21 | `tools/proof_audit.py` Batch 10 | 92 | 84 | 0 | 8 | 0 | +15 OWASP Top 10:2025 web + LLM harnesses; `make proof` 92/92, `make test` 5100 tests OK. |
| 2026-06-27 | `tools/proof_audit.py` Batch 11 | 100 | 92 | 0 | 8 | 0 | +8 exploratory + deferred-OWASP harnesses (combinatorial_coverage, counterexample_replay, stateful_sequence_budget, boundary_corpus_expander; data_integrity A08; data_poisoning LLM04; system_prompt_leakage LLM07; misinformation LLM09); `make proof` 92/92. |

## Update rules

- Do not update this file from memory.
- Do not update this file from `README.md` alone.
- Do not update this file unless the proof/status source is named.
- If this file disagrees with proof output, proof output wins.
- If this file disagrees with `README.md`, fix the stale doc in the same PR.
- Do not edit generated proof counts manually; regenerate or cite the source that produced them.

## Interpretation

Inventory count is not proof strength.

Use:

- `cards/teeth_ratchet.json` for required, pending, and legacy class, when available;
- generated `STATUS.md` / `STATUS.json` artifacts for current run output;
- proof audit output for the actual proof result;
- harness cards and proof tests for porting notes and limits.
