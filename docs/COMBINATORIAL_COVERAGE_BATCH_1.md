# Combinatorial coverage batch 1

Status: batch note for PR #88.
Date: 2026-06-27.

## Scope

This batch adds the first exploratory proof-layer harness:

- `harnesses/core/combinatorial_coverage_test_harness.py`
- `tests/core/test_combinatorial_coverage_test_harness.py`

The harness uses a tiny finite parameter model and independently audits pairwise interaction coverage.

## Proof shape

The known-good suite covers every required pairwise interaction for the declared finite model.

Planted mutants are caught when they:

- drop a case and lose required interactions;
- collapse a parameter value and lose interactions;
- omit a required parameter from every case.

## Claim boundary

This batch does not claim exhaustive testing for real applications. It proves pairwise/t-way coverage accounting for a declared finite model.

## Source check

Evidence-only sources checked:

- Python `itertools`: `https://docs.python.org/3/library/itertools.html`
- Python `unittest`: `https://docs.python.org/3/library/unittest.html`
- NIST combinatorial testing project: `https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software`

Repo-local rules and current operator instruction remain authority.

## Closeout boundary

`README.md` and `HARNESS_ROADMAP.md` are updated for the new count and batch status.

`HARNESS_INVENTORY.md` and `docs/HARNESS_MAP.md` are intentionally deferred to a later mapping/closeout PR or a full local checkout because they are large catalog files and should not be blind full-replaced from connector-truncated reads.
