# Reviewer Quickstart

This repo is a standard-library test-harness library. The useful question is
not "does this prove all code is correct?" It does not. The useful question is
"does each harness demonstrate a reproducible safe case and a reproducible bad
case for a specific bug class?"

## What the repo proves

- The current proof baseline can discover 73 real harnesses.
- Each discovered harness has a paired `unittest` file.
- Each discovered harness currently passes its `--self-test` under the proof
  audit command.
- Each discovered harness has proof evidence from a proof file, embedded
  controls, or self-test output.
- The harnesses are small, local, and inspectable enough for reviewers to trace
  the safe fixture and the planted bad fixture.

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

For a docs-only review, the minimum local check is:

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
2. Open the harness file under `harnesses/<category>/`.
3. Find the safe fixture or reference implementation.
4. Find the planted bad fixture, buggy implementation, or negative control.
5. Open the paired test under `tests/<category>/`.
6. Confirm the paired test covers both API behavior and CLI/self-test behavior
   where the harness exposes a CLI.
7. Run the harness self-test directly.

Example:

```bash
python harnesses/core/statistical_rng_oracle_test_harness.py --self-test
python -m unittest tests.core.test_statistical_rng_oracle_test_harness tests.core.test_statistical_rng_oracle_proof
```

Expected reviewer result: the safe fixture passes, the biased RNG control fails,
and the docs claim only that this catches CI-sized RNG bias and replay mistakes.
It does not claim casino certification or full game economy validation.
