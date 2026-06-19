# Anti-vacuity model

A weak test passes because the implementation is correct.

A dangerous test passes even when the implementation is wrong.

This repo treats a test as stronger only when it proves that known-bad behavior is detected.

## Thesis

A passing test is not strong evidence unless the same test structure can catch a planted-bad implementation.

The target failure is not merely a red test. The target failure is false confidence: a green test suite that would stay green after the bug is introduced.

## What vacuity means here

A vacuous test is a test that appears to check behavior but does not actually constrain the bug class it claims to cover.

Common causes:

- the test only checks that code runs;
- the test repeats the implementation logic;
- the mock proves its own behavior;
- the assertion is too broad;
- the negative case is absent;
- the fixture is too narrow;
- exceptions are swallowed;
- generated coverage is mistaken for behavioral proof.

## Bad-test taxonomy

| Failure type | Meaning | Countermeasure |
|---|---|---|
| Happy-path-only | Valid input passes, invalid behavior untested | Add planted-bad case |
| Oracle echo | Test repeats implementation logic | Freeze expected events separately |
| Assertion-free test | Code runs but checks nothing | Require explicit expected/actual |
| Mock tautology | Mock proves itself | Test contract, not mock internals |
| Broad exception swallow | Failure becomes pass | Require fail-loud report contract |
| Coverage theater | Lines execute, behavior remains unproven | Add mutants / TEETH proof |
| Overfit fixture | One handpicked case passes | Add corpus variation |
| Flaky pass | Result changes without code change | Control clock, randomness, files, and network |
| AI hallucinated requirement | Test checks invented behavior | Require contract/source note |
| Green theater | CI passes but risk remains untested | Use proof-strength ladder |

## How TEETH counters vacuity

A TEETH-shaped harness is stronger when:

1. the correct oracle is not falsely caught;
2. every planted mutant is caught;
3. the corpus is nonempty;
4. proof is reproducible;
5. self-test reports fail loudly.

This does not prove total correctness. It proves that the harness can detect the known-bad behavior it claims to detect.

## What not to infer

Do not infer that:

- a harness covers every variant of the bug class;
- a target application is safe after porting one harness;
- coverage percentage equals proof strength;
- a mock-only proof replaces production integration tests;
- property-based testing replaces planted-bad proof.

## Porting implication

When porting a harness, copy the proof shape, not just the code.

The portable proof kernel is:

- contract;
- known-good case;
- planted-bad case;
- independent oracle or frozen expected result;
- nonempty corpus;
- deterministic proof check;
- explicit known limits.
