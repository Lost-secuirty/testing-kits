# testing-kits

Pure-Python, standard-library-first testing harness collection.

## Current state

- **72 harnesses** across core reliability, security, AI, and pharmacy-domain testing.
- Each harness is a self-contained Python file.
- Each harness has a paired `unittest` suite.
- Harnesses expose `--self-test` when applicable.
- Batch 7 added proof-backed harnesses with planted-bad-fixture checks where applicable.
- `STATUS.md` is generated output; the