# Proof strength ladder

The repo does not treat proof as binary.

A green run can mean several different things. The useful question is what kind of proof the test structure provides.

## Level 0 — Example only

The file demonstrates an idea.

Expected evidence:

- readable example;
- no self-test requirement;
- no planted-bad requirement.

Risk:

- educational value only;
- not enough evidence for proof claims.

## Level 1 — Self-tested

The harness can run and report pass/fail.

Expected evidence:

- CLI or callable self-test;
- nonzero failure path where applicable;
- paired smoke test or direct execution path.

Risk:

- the self-test may only prove that the harness can execute.

## Level 2 — Negative-tested

Known-bad cases exist.

Expected evidence:

- known-good fixture;
- known-bad fixture;
- explicit expected result;
- paired unittest coverage.

Risk:

- the oracle may still echo implementation logic;
- the negative case may be too narrow.

## Level 3 — TEETH-proven

The harness is checked against the TEETH proof contract.

Expected evidence:

- correct oracle stays clean;
- every planted mutant is caught;
- corpus is nonempty;
- proof is deterministic;
- self-test fails loudly;
- gate blocks required harnesses that violate the contract.

Risk:

- proof is limited to the declared contract and corpus.

## Level 4 — Port-hardened

The harness explains how to adapt the proof shape without overclaiming.

Expected evidence:

- contract statement;
- failure class;
- portable core;
- non-contract scaffolding;
- known limits;
- porting notes;
- failure example or expected bad behavior.

Risk:

- target projects can still omit integration, real dependency tests, or domain-specific review.

## Level 5 — Release-grade

The repo state is ready to cite as a release-quality proof snapshot.

Expected evidence:

- CI green;
- proof audit green;
- generated docs/status synced;
- branch protection/check names verified;
- reproducibility steps documented;
- release artifact or tag process reproducible, if a release is being made.

Risk:

- release-grade for this repo is not production certification for downstream software.

## Interpretation rule

Inventory count is not proof strength.

A repo with many harnesses can still be weak if the harnesses are example-only. A smaller repo with planted-bad proof and reproducible failure evidence can be stronger for the covered bug classes.

## Minimum language for claims

Use precise wording:

- "TEETH-proven for the declared contract" is acceptable when the proof audit supports it.
- "Proves the application is safe" is not acceptable.
- "Covers this entire security class" is not acceptable unless the contract truly defines that scope.
- "Catches the planted mutant in the frozen corpus" is acceptable when verified.
