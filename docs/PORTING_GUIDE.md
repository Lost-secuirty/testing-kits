# Porting guide

Copy the proof shape, not just the code.

A harness is portable because its proof structure is portable. The local file paths, mock ports, fixture names, and CLI details are scaffolding.

## Porting principle

When adapting a harness into another project, preserve the contract:

- what behavior must hold;
- what bad behavior must be rejected;
- what oracle decides pass/fail;
- what planted-bad case proves the test can fail;
- what limits remain after the proof passes.

## Required pieces

A useful port needs:

- failure class;
- contract;
- known-good case;
- planted-bad case;
- frozen audit corpus or deterministic fixture set;
- independent oracle or frozen expected result;
- self-test mode or equivalent fail-loud path;
- proof test;
- deterministic clock, randomness, files, and network where relevant;
- explicit known limits.

## Do not blindly copy

Do not blindly copy:

- mock server ports;
- synthetic fixture names;
- local CLI details;
- repo-specific paths;
- generated proof counts;
- pharmacy-domain assumptions;
- README status snapshots;
- dashboard dependency behavior.

## Port checklist

- [ ] What failure class is being tested?
- [ ] What contract does the target project actually need?
- [ ] What does the good case prove?
- [ ] What does the planted-bad case prove?
- [ ] What would a bad implementation do?
- [ ] What would a vacuous test look like?
- [ ] Does the correct oracle stay clean?
- [ ] Is every planted mutant caught?
- [ ] Is the corpus nonempty?
- [ ] Are time, randomness, files, and network controlled?
- [ ] Are external inputs treated as hostile data?
- [ ] Does the target repo need real dependency integration?
- [ ] Does the target repo need property-based expansion?
- [ ] Did the target repo run its native tests?
- [ ] Are known limits preserved in the ported docs?

## Portable core vs project-specific proof

### Portable core proof

The portable core is the smallest reproducible proof unit:

- frozen fixture corpus;
- oracle;
- known-good behavior;
- planted-bad behavior;
- deterministic proof command;
- explicit failure report.

### Project-specific production proof

Target projects usually need more:

- real database or broker integration;
- real API sandbox or contract server;
- real authentication/authorization configuration;
- property-based input expansion;
- production observability and alerting;
- domain review.

Keep these layers separate. `testing-kits` gives the proof kernel; the target project proves its own production assumptions.

## Example: JWT harness boundary

A portable JWT harness may prove that a verifier rejects specific bad token shapes such as:

- unsigned `alg=none` tokens;
- tampered payloads;
- expired tokens;
- missing required claims.

That does not automatically prove:

- issuer validation;
- audience validation;
- JWK rotation;
- asymmetric-key behavior;
- production key-management safety;
- every JWT library edge case.

If the target application depends on those behaviors, add target-specific tests.

## Porting output contract

Every port should state:

- source harness used;
- target contract;
- files touched;
- behavior changes, if any;
- proof command run;
- native test command run;
- known limits;
- assumptions;
- next layer needed.
