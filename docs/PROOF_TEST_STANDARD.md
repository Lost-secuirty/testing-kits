# Proof Test Standard

Every new harness must prove two things:

1. The safe fixture passes.
2. The planted bad fixture fails.

The minimum shape is:

- `harnesses/<category>/<name>_test_harness.py` — self-contained harness with `--self-test`.
- `tests/<category>/test_<name>_test_harness.py` — API and CLI tests.
- `tests/<category>/test_<name>_proof.py` — planted-bug proof when the paired test alone cannot prove failure detection.

Coverage can show code was exercised, but it does not prove the test would catch a real bug. Use proof fixtures or mutation probes for that.
