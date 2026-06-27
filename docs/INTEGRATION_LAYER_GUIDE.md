# Integration layer guide

`testing-kits` provides the portable proof kernel.

Target repositories provide production-context proof.

Do not collapse those layers.

## Position

The core repo should not become a container-heavy integration test framework.

Its value is portability, inspectability, and pure-stdlib proof examples. Real dependency tests belong in `DEP-TEST-KIT` or in the target project that owns the dependency surface.

## When mocks are enough

Mocks are useful when the goal is to prove the local test shape:

- known-good behavior passes;
- planted-bad behavior fails;
- oracle catches the intended bug class;
- self-test reports clearly;
- proof can run with no external service.

This is correct for a portable reference repo.

## When mocks are not enough

Mocks are insufficient when the target risk depends on real integration behavior:

- database isolation or migration behavior;
- broker delivery semantics;
- cache eviction or distributed lock behavior;
- browser rendering and accessibility behavior;
- third-party API behavior;
- authentication provider configuration;
- network timeout and retry behavior;
- container startup/health behavior.

## Examples

| Portable reference | Target-project next layer |
|---|---|
| SQLite reference harness | Postgres/MySQL integration test |
| mock Redis behavior | real Redis container or managed test service |
| in-process queue oracle | real broker test |
| static browser/E2E reference | Playwright/Selenium/browser container |
| mock HTTP server | real API sandbox or contract test server |
| toy JWT corpus | target auth provider and key-rotation tests |
| static PII scanner fixture | production log/export pipeline sampling |

## Recommended split

Use `testing-kits` for:

- portable proof shape;
- contract examples;
- planted-bad controls;
- anti-vacuity reference patterns;
- reader and porting documentation.

Use `DEP-TEST-KIT` or target repos for:

- dependency scanners;
- containerized services;
- dependency-review checks;
- SBOM/provenance checks;
- real framework integration tests;
- production observability.

## Porting checklist

- [ ] Does the portable harness prove the known-bad behavior?
- [ ] Does the target project use a real dependency that changes the risk?
- [ ] Is a mock hiding an integration-specific failure mode?
- [ ] Does the target project need service startup/teardown control?
- [ ] Does the target project need seeded data or migrations?
- [ ] Does the target project need testcontainers or equivalent tooling?
- [ ] Are slow integration tests separated from fast proof-kernel tests?
- [ ] Are dependency-specific assumptions documented?

## Claim boundary

Acceptable:

```text
This harness demonstrates the proof shape for this failure class.
```

Acceptable after target tests exist:

```text
The target project adds dependency integration coverage for this same contract.
```

Not acceptable:

```text
The pure-stdlib mock proves the production dependency is safe.
```
