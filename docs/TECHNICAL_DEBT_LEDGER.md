# Technical debt ledger

This file records accepted incompleteness.

Technical debt is less dangerous when it is named, scoped, and assigned a review trigger.

## TD-001: Pharmacy harnesses remain legacy

Reason:

The pharmacy harnesses are domain-specific and do not share the same generic TEETH proof category as the core/security/AI campaign.

Risk:

Readers may mistake them for generic required harnesses.

Mitigation:

Keep them labeled legacy. Do not count them as generic required proof unless a domain-specific proof model is added.

Review trigger:

If pharmacy repo work resumes or if domain proof migration is planned.

## TD-002: Core repo remains pure stdlib

Reason:

The repo's value is portable, inspectable proof examples with zero runtime dependencies for the harness collection.

Risk:

The core repo does not prove real container, database, broker, browser, or provider behavior.

Mitigation:

Document the integration boundary. Place real dependency tests in `DEP-TEST-KIT` or target repos.

Review trigger:

If the repo's purpose changes from proof-kernel reference library to dependency-backed framework.

## TD-003: Property-based testing is a porting layer

Reason:

Property-based tools are useful but introduce dependency and runtime complexity that does not belong in the core portable repo by default.

Risk:

Users may think frozen planted-bad proof is enough for broad input spaces, or that property tests can replace planted-bad controls.

Mitigation:

Document property-based expansion as a target-project layer that complements TEETH.

Review trigger:

If a dependency-backed companion repo is created for property-based variants.

## TD-004: Failure examples are manually curated

Reason:

Failure examples teach the proof model faster than abstract language, but they are docs unless generated from observed command output.

Risk:

Readers may mistake illustrative examples for current proof output.

Mitigation:

Label examples as explanatory unless they explicitly cite observed output.

Review trigger:

If failure-output generation is automated.

## TD-005: Human docs need sync checks

Reason:

The repo intentionally has human-readable docs, AI-readable docs, inventories, campaign records, generated status artifacts, and cards.

Risk:

Counts or proof states can drift across files.

Mitigation:

Use `docs/GOLDEN_STATS.md` as a quick reference and proof/generator output as source of truth. Add docs sync tooling later if needed.

Review trigger:

If another proof campaign changes required/pending/legacy counts.
