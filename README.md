# testing-kits

Pure-Python standard-library testing-harness collection. **Zero runtime dependencies.**

Current public shape:

- **72 harnesses** across reliability, security, AI, and pharmacy-domain testing.
- Each harness is a single self-contained Python file.
- Each harness has a paired `unittest` suite.
- New proof-backed harnesses include planted bad fixtures to prove the harness catches the intended failure class.
- Harnesses expose a built-in `--self-test` mode when applicable.
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
python harnesses/core/api_test_harness.py --self-test
python harnesses/ai/drift_detection_test_harness