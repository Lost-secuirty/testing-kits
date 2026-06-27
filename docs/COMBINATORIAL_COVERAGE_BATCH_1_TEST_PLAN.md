# Combinatorial coverage batch 1 test plan

Status: temporary test-plan note for PR #88.
Date: 2026-06-27.

## Commands to verify

```bash
python -m unittest tests.core.test_combinatorial_coverage_test_harness
python harnesses/core/combinatorial_coverage_test_harness.py --self-test
python harnesses/core/combinatorial_coverage_test_harness.py --json
python harnesses/core/combinatorial_coverage_test_harness.py --list-scenarios
make test
make selftest
make proof
```

## Expected proof shape

- `prove(oracle_pairwise_suite)` returns `False`.
- `prove(missing_interaction_mutant)` returns `True`.
- `prove(collapsed_value_mutant)` returns `True`.
- `prove(omitted_parameter_mutant)` returns `True`.

## Known limits

- Tiny finite model only.
- Pairwise/t-way coverage accounting only.
- No claim of exhaustive testing for real applications.
- No external dependency or coverage-guided fuzzer.
