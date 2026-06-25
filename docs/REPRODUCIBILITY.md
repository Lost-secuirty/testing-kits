# Reproducibility

This repo should not only say that proof is green. It should say how to replay the proof state.

## Current proof replay

Run the normal checks from the repo root:

```bash
python -m unittest
make selftest
make proof
make report
```

Optional or targeted checks may include:

```bash
python -m py_compile tools/*.py
python cards/harness_card.py --check
python tools/scan_staged.py --self-test
python tools/scan_staged.py
git diff --check
```

## Expected documented snapshot

As of the documented Batch 10 snapshot, dated 2026-06-21:

- 92 total harnesses;
- 84 required;
- 0 pending;
- 8 legacy;
- 0 failing.

This is a documented snapshot, not a fresh proof claim. Rerun proof before using it as release evidence. See `docs/GOLDEN_STATS.md` and `docs/UPGRADE_CAMPAIGN.md` for the source trail.

## Reproducing a single harness

1. Read the harness card or inventory entry.
2. Read the harness implementation.
3. Read the paired unittest.
4. Read the proof test, if present.
5. Run the paired unittest.
6. Run the harness self-test, if applicable.
7. Confirm the planted mutant or known-bad fixture is caught.
8. Confirm the safe oracle stays clean.

## Example shape

```bash
python harnesses/core/statistical_rng_oracle_test_harness.py --self-test
python -m unittest tests.core.test_statistical_rng_oracle_test_harness tests.core.test_statistical_rng_oracle_proof
```

Use the actual harness and test names for the selected file.

## Reproducing a docs claim

If a doc claims a count, verify it against a source:

- proof audit output;
- generated status artifact;
- `cards/teeth_ratchet.json`, if present;
- current CI/test output;
- campaign record with date and batch.

Do not manually trust stale documentation.

## Reproducing failure evidence

Failure examples in `docs/failure-examples/` are explanatory unless they explicitly cite observed output.

When converting a failure example into proof evidence, record:

- command;
- branch;
- commit;
- expected failure;
- observed failure;
- whether the correct oracle stayed clean.

## Command reporting

Use exact wording in PRs:

```md
## Verification

Run:
- `python -m unittest`
- `make selftest`
- `make proof`
- `make report`

Not run:
- `<command>` — reason: `<reason>`
```

Do not write vague claims such as "tests passed" without the command list.

## Freshness rule

A proof snapshot becomes stale when code, tests, generated registries, proof tools, or harness classifications change.

After such a change, rerun the relevant proof commands or state that proof was not rerun.
