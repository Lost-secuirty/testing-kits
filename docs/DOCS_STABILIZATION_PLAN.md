# Docs stabilization plan

This is the rollout checklist for the docs-only AI-human porting and proof-guidance pass.

The goal is not to make the repo larger by adding harnesses. The goal is to make the existing proof model harder to misread, misport, or overclaim.

## Scope

Allowed:

- docs;
- reader paths;
- proof-language clarification;
- porting guides;
- AI-consumption guidance;
- agent handoff guidance;
- observability and reproducibility docs;
- failure examples;
- human-maintained snapshot references;
- doc-size and connector-safety guidance.

Not allowed in this pass:

- new harness inventory;
- harness behavior changes;
- runtime dependencies;
- generated proof-count edits without generator output;
- production-framework claims;
- container/service dependencies in the core repo;
- main-branch writes;
- merge without explicit authorization.

## Batch 0 — Baseline and drift correction

- [x] Add `docs/GOLDEN_STATS.md`.
- [x] Update README's stale Batch 5 proof snapshot.
- [x] Add `docs/DOC_SIZE_POLICY.md` after connector truncation appeared on `HARNESS_INVENTORY.md`.
- [ ] Add inventory-count versus proof-strength note to `HARNESS_INVENTORY.md`.

Status:

`HARNESS_INVENTORY.md` still needs a top-section note if a safe full-file edit path is available. Do not overwrite it from a truncated read. If no safe patch path is available, leave the large inventory untouched and keep the source-of-truth boundary in `README.md`, `docs/GOLDEN_STATS.md`, and `docs/DOC_SIZE_POLICY.md`.

## Batch 1 — Reader path

- [x] Add `docs/START_HERE.md`.
- [ ] Link reader-path docs from README.

## Batch 2 — Proof language

- [x] Add `docs/ANTI_VACUITY_MODEL.md`.
- [x] Add `docs/PROOF_STRENGTH_LADDER.md`.
- [ ] Link proof-language docs from README.

## Batch 3 — Porting protocol

- [x] Add `docs/PORTING_GUIDE.md`.
- [x] Add `docs/PROPERTY_BASED_PORTING.md`.
- [x] Add `docs/INTEGRATION_LAYER_GUIDE.md`.
- [ ] Link porting docs from README.

## Batch 4 — AI and agent guidance

- [x] Add `docs/AI_CONSUMPTION_GUIDE.md`.
- [x] Add `docs/AGENT_COMMUNICATION_GUIDE.md`.
- [ ] Link AI docs from README.

## Batch 5 — Observability, reproducibility, debt, and failure examples

- [x] Add `docs/TEST_OBSERVABILITY.md`.
- [x] Add `docs/REPRODUCIBILITY.md`.
- [x] Add `docs/TECHNICAL_DEBT_LEDGER.md`.
- [x] Add at least three controlled failure examples.
- [ ] Link these docs from README.

## Final PR checklist

- [ ] PR is draft.
- [ ] PR is docs-only.
- [ ] No harness behavior changed.
- [ ] No runtime dependencies added.
- [ ] No generated `STATUS.md` or `STATUS.json` committed.
- [ ] Every proof count names its source.
- [ ] README links to the new reader path.
- [ ] Large docs are not overwritten from truncated connector reads.
- [ ] Commands run are listed exactly.
- [ ] Commands not run are listed with reasons.
- [ ] CI/proof result is not overclaimed.

## Final verification commands

Run once near the end of the PR, not after every docs batch:

```bash
python -m py_compile tools/*.py
python -m unittest
make selftest
make proof
make report
python cards/harness_card.py --check
python tools/scan_staged.py --self-test
python tools/scan_staged.py
git diff --check
```

If any command is unavailable, record it in the PR body under `Not run` with a reason.
