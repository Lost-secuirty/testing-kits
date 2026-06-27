# Combinatorial coverage batch 1 review notes

Status: compact reviewer note for PR #88.
Date: 2026-06-27.

## What changed

- Added `harnesses/core/combinatorial_coverage_test_harness.py`.
- Added `tests/core/test_combinatorial_coverage_test_harness.py`.
- Added `docs/COMBINATORIAL_COVERAGE_BATCH_1.md`.
- Updated `README.md` and `HARNESS_ROADMAP.md` count/status wording.

## Review focus

Check that the harness proves coverage accounting, not exhaustive behavior:

- oracle suite covers every required pairwise interaction for the declared model;
- missing-interaction mutant is caught;
- collapsed-value mutant is caught;
- omitted-parameter mutant is caught;
- no external dependency is introduced.

## Deferred docs

`HARNESS_INVENTORY.md` and `docs/HARNESS_MAP.md` are intentionally deferred to a later mapping/closeout PR or a full local checkout to avoid full replacement from connector-truncated reads.
