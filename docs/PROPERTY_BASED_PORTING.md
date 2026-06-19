# Property-based porting

TEETH and property-based testing answer different questions.

TEETH proves that known bads are caught.

Property-based testing searches around a behavior space for unknown edge cases.

They are complementary. Property-based testing should not replace planted-bad proof.

## Core repo boundary

The core `testing-kits` repo stays pure Python standard library.

Do not add property-based testing dependencies to the core harness collection just to make the examples larger.

When porting into a production project, use the target project's ecosystem:

- Python: consider Hypothesis;
- JavaScript/TypeScript: consider fast-check;
- other stacks: use the native property-based or fuzzing tool that fits the project.

## Correct layering

Use TEETH for:

- known-good fixture;
- known-bad fixture;
- planted mutant;
- frozen audit corpus;
- deterministic proof replay.

Use property-based testing for:

- expanding input variation;
- checking invariants across many generated cases;
- boundary exploration;
- regression discovery after new bugs are found.

## Do not replace planted bads

Generated inputs can miss the exact known-bad behavior unless the generator is designed to include it.

A property test that never generates the dangerous case can pass forever while the known bug remains exploitable.

## Example: JWT port

TEETH cases might include:

- `alg=none`;
- tampered payload;
- expired token;
- missing required claim.

Property expansion might include:

- random claim dictionaries;
- random timestamps around expiry boundaries;
- malformed base64url segments;
- invalid JSON payloads;
- unexpected algorithm strings;
- duplicated claims;
- non-string claim values.

The generated cases should wrap around the same contract. They should not invent a new, undocumented requirement.

## Example: PII redaction port

TEETH cases might include:

- known phone-number leak;
- known SSN leak;
- known account-number leak;
- safe text that must not be over-redacted.

Property expansion might include:

- random digit grouping;
- punctuation variation;
- Unicode separators;
- mixed safe/unsafe strings;
- long payloads with sparse secrets.

The planted-bad leak remains required.

## Port checklist

- [ ] Does the TEETH proof still catch the known bad?
- [ ] Does the property generator include boundary cases relevant to the contract?
- [ ] Are generated failures reproducible by seed or minimized example?
- [ ] Is the oracle independent enough to avoid echoing implementation logic?
- [ ] Are generated requirements traceable to the target contract?
- [ ] Are slow or flaky generated tests separated from the fast proof kernel?

## Failure mode

Bad pattern:

```text
Delete planted-bad tests because property-based tests generate many inputs.
```

Better pattern:

```text
Keep planted-bad tests as regression anchors.
Add property-based tests around the same oracle.
Log minimized counterexamples as future deterministic fixtures.
```
