# AI-Authored Test Audit Checklist

AI-assisted tests are useful, but the authorship is not evidence. Review the
test like untrusted code until its safe and bad controls behave as expected.

## Triage checklist

- The harness names one specific bug class it catches.
- The safe fixture passes for the intended reason.
- The planted bad fixture fails for the intended reason.
- The bad fixture is deterministic and does not rely on timing, randomness, or
  network behavior unless the harness explicitly controls those inputs.
- The paired `unittest` covers the public API.
- The paired `unittest` covers CLI exit behavior when a CLI exists.
- The test would fail if the core check were deleted, inverted, or replaced by
  a no-op.
- Coverage is not treated as proof by itself.
- Claims in docs match the actual fixture behavior.
- Limitations and unsampled areas are stated when the harness is narrow.

## Common AI-test failure modes

| Failure mode | What to look for | Acceptable evidence |
| --- | --- | --- |
| Silent failure | Test passes even when the implementation does nothing useful. | Planted bad fixture fails, and a no-op or inverted check would be caught. |
| Hallucinated assumption | Test assumes APIs, schemas, tools, or threat models that are not in the repo. | Harness imports only real code or uses explicit local fixtures. |
| Weak fixture | Safe and bad fixtures are too similar or do not exercise the named risk. | Bad fixture breaks exactly the claimed invariant. |
| Fake coverage | Lines execute, but no assertion proves the failure mode. | Assertions check returned status, reasons, metrics, or exit code. |
| Missing planted-bad control | Test only shows the happy path. | Proof file, paired test, or self-test rejects a deliberately bad case. |
| Wrong claim | Docs say the harness proves a broader class than it actually tests. | Wording is scoped to the fixture-defined bug class. |

## Review procedure

1. Start with `python tools/proof_audit.py --run-selftests`.
2. Pick high-risk samples first: `ai`, `security`, concurrency, mutation,
   statistical RNG, game loop, and pharmacy-domain software checks.
3. Trace each sampled harness from docs to implementation to paired test.
4. Confirm the safe and planted-bad controls are visible in code.
5. Fix wording before adding tests if the code is correct but the claim is too
   broad.
6. Add or improve tests only when the current proof evidence does not show the
   bad case being rejected.

## Pharmacy-domain wording rule

Pharmacy-domain harnesses are software-fixture checks. They may describe the
rule encoded in the fixture and the planted bad behavior caught by the harness.
They must not imply clinical validation, medication-safety certification,
production readiness, or pharmacy-grade correctness.
