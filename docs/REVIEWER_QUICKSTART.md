# Reviewer Quickstart

This repo is a standard-library test-harness library. The useful question is
not "does this prove all code is correct?" It does not. The useful question is
"does each harness demonstrate a reproducible safe case and a reproducible bad
case for a specific bug class?"

If you are new to the repo, read `docs/READER_LEVELS.md` first. It separates
beginner, junior reviewer, and senior auditor paths so a review does not require
loading the whole documentation set.

## What the repo proves

- The current inventory contains **92 harnesses**.
- Each harness is intended to remain small, local, and inspectable enough for a
  reviewer to trace the safe fixture and the planted-bad fixture.
- The documented Batch 10 TEETH snapshot distinguishes harnesses by status instead
  of treating all 92 as equivalent:
  - **required** — a non-legacy harness with `TEETH`; the swap-check verifies that
    the correct oracle is not flagged and every planted mutant is caught.
  - **pending** — a non-legacy harness that is counted in the inventory but has
    not yet been ratcheted into the required TEETH contract.
  - **legacy** — pharmacy-domain harnesses still tracked under the older soft gate.
- The documented Batch 10 proof-ratchet snapshot is **84 required / 0 pending / 8
  legacy / 0 failing**. Re-run `make proof` before treating that as a fresh
  release claim.
- The proof baseline is fixture-defined. It shows known-good cases pass and
  planted-bad cases fail under the repo's current tooling; it is not total
  correctness proof.

## What the repo does not prove

- It does not prove total correctness for any target application.
- It does not prove that a future AI-generated change is safe.
- It does not replace human review, domain review, or production monitoring.
- Pharmacy-domain harnesses are fixture-defined software checks only. They are
  not clinical validation, medication-safety certification, or pharmacy-grade
  correctness assurance.
- AI-authored or AI-assisted tests are not trusted by authorship. They are only
  trusted when safe fixtures and planted bad controls demonstrate the expected
  pass/fail behavior.

## Core verification commands

On systems with `make`:

```bash
make test
make selftest
make proof
```

Direct Python commands, useful on Windows when `make` is unavailable:

```bash
python -m unittest discover -s tests -t . -p "test_*.py"
python tools/generate_report.py --check
python tools/proof_audit.py --run-selftests
python tools/scan_staged.py --self-test
```

For a docs-only review, the minimum local check is a file-scope review: confirm
only documentation files changed and no generated `STATUS.md` / `STATUS.json`
artifacts were committed. If command execution is available, run:

```bash
python tools/proof_audit.py --run-selftests
python tools/generate_report.py --check
python tools/scan_staged.py --self-test
```

Run self-test/report commands sequentially. Some harnesses use local mock
servers, so running multiple all-harness sweeps at the same time can create a
port collision that is not a harness failure.

## Inspect one harness

Use one harness as a traceable sample before trusting the inventory.

1. Pick a harness from `HARNESS_INVENTORY.md`.
2. Check `docs/HARNESS_MAP.md` if the harness has already been mapped; map
   entries explain failure class, logic shape, outside testing pattern, proof
   status, and known limits.
3. Open the harness file under `harnesses/<category>/`.
4. Find the safe fixture or reference implementation.
5. Find the planted bad fixture, buggy implementation, or negative control.
6. Open the paired test under `tests/<category>/`.
7. Confirm the paired test covers both API behavior and CLI/self-test behavior
   where the harness exposes a CLI.
8. If the harness is `required`, inspect its `TEETH` declaration and confirm the
   proof predicate judges the implementation against fixed fixtures rather than
   re-deriving expected behavior from the oracle at runtime.
9. Run the harness self-test directly.

Example:

```bash
python harnesses/core/statistical_rng_oracle_test_harness.py --self-test
python -m unittest tests.core.test_statistical_rng_oracle_test_harness tests.core.test_statistical_rng_oracle_proof
```

Expected reviewer result: the safe fixture passes, the biased RNG control fails,
and the docs claim only that this catches CI-sized RNG bias and replay mistakes.
It does not claim casino certification or full game economy validation.

## Wording check for docs reviews

Use `docs/DOC_STYLE_GUIDE.md` when reviewing documentation wording. The shortest
acceptable claim form is:

```text
This harness shows [known-good behavior] and catches [planted-bad behavior] for [specific failure class]. It does not prove [broader system claim].
```
