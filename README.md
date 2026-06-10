# testing-kits

Pure-Python standard-library testing-harness collection. **Zero runtime dependencies.**

Current public shape:

- **72 harnesses** across reliability, security, AI, and pharmacy-domain testing.
- Each harness is a single self-contained Python file.
- Each harness has a paired `unittest` suite.
- Harnesses expose a built-in `--self-test` mode when applicable.
- Batch 7 adds proof-backed harnesses with planted-bad-fixture checks where applicable.
- The repo is designed as a reusable reliability/testing-pattern library, not a production framework.

## Start here

Fast reviewer path:

```bash
python --version
make test
make selftest
```

Single-harness smoke checks:

```bash
python harnesses/core/api_test_harness.py --