# Exploratory proof layer

Status: planning document.
Scope: docs-only plan for future pure-stdlib harness upgrades.

This document explains how exploratory testing can extend the repo's proof surface without weakening the existing TEETH model.

## Purpose

`testing-kits` already proves known-bad behavior through small deterministic harnesses. That remains the primary proof model.

The next upgrade layer should add controlled exploration around those contracts:

- deterministic boundary sweeps;
- combinatorial/t-way coverage for finite parameter spaces;
- property/invariant checks;
- stateful sequence exploration;
- counterexample freezing and replay.

The goal is not to prove every possible input. The goal is to find more edge cases while preserving reproducible planted-bad proof.

## Non-goals

Do not use this layer to claim:

- total correctness;
- all combinations tested for real software;
- production assurance;
- application security certification;
- clinical or pharmacy-grade validation;
- replacement of TEETH, planted-bad fixtures, or human review.

Do not add Hypothesis, Atheris, pytest, coverage.py, or other dependencies to the core harness collection just to make exploratory examples larger. Dependency-backed exploration belongs in downstream target repos or explicitly scoped integration layers.

## Why exhaustive testing is not the target

Exhaustive testing is only realistic for explicitly finite toy spaces.

Real software combines too many dimensions:

- types;
- numeric ranges;
- strings;
- encodings;
- object shapes;
- clocks;
- retries;
- permissions;
- external state;
- action order;
- concurrency;
- environment differences.

Even small finite models grow quickly. Ten boolean switches already create 1,024 combinations. Twenty create 1,048,576. Adding strings, dictionaries, and state transitions makes full enumeration impractical.

The repo-safe claim is narrower:

```text
Exploratory harnesses expand input and sequence coverage for declared contracts.
They do not prove complete behavior for arbitrary target applications.
```

## Layer model

### Layer 1 - TEETH known-bad proof

TEETH remains the anchor.

Use TEETH for:

- known-good fixtures;
- planted-bad fixtures;
- planted mutants;
- frozen literal corpora;
- deterministic replay;
- proof that the bad behavior is caught.

Generated-input checks may find new examples, but a finding becomes a strong repo claim only after it is frozen into deterministic proof evidence.

### Layer 2 - deterministic boundary sweeps

Boundary sweeps enumerate reviewed edge values around a contract.

Examples:

- `None`;
- empty strings and containers;
- zero and negative one;
- minimum and maximum boundaries;
- Unicode separator and normalization cases;
- malformed records;
- duplicate keys or identifiers;
- values just inside and just outside allowed ranges.

Boundary sweeps should be deterministic. If a boundary case matters, name it and keep it stable.

### Layer 3 - combinatorial/t-way coverage

Combinatorial testing covers interactions among small sets of parameters without trying every full Cartesian product.

For a finite parameter model, a t-way harness should prove things like:

- every single value appears at least once;
- every pair of parameter values appears at least once for pairwise coverage;
- every triple appears at least once for 3-way coverage, when justified;
- coverage accounting fails when a required interaction is missing.

This layer is useful for configuration, feature flags, validators, policy matrices, role/action/object triples, and other finite modeled spaces.

Limits:

- proves coverage accounting for the declared finite model only;
- does not prove the target implementation is correct without an oracle or invariant;
- does not replace domain-specific review.

### Layer 4 - property/invariant checks

A property is a rule that should hold across many generated cases.

Examples:

- normalizing twice equals normalizing once;
- encode/decode round trip preserves reviewed fields;
- sorting twice equals sorting once;
- a redactor never leaks a frozen secret pattern;
- invalid transition attempts leave state unchanged.

For this repo, property-like harnesses should stay pure stdlib and deterministic. Downstream projects may use their native property-based testing tools when dependencies are acceptable.

### Layer 5 - stateful sequence exploration

Some failures appear only after action order matters.

A stateful exploration harness models:

- allowed states;
- actions;
- transitions;
- terminal states;
- invalid transitions;
- per-run budgets;
- visited state/action paths.

The harness must stop on budget, terminal state, or exhaustion. It must not depend on live time, threads, network calls, filesystem state, or unseeded randomness in `prove`.

### Layer 6 - counterexample freezing

Exploratory testing becomes durable when a failure is minimized or frozen into a deterministic fixture.

A counterexample record should preserve:

- input value or action sequence;
- seed, when generation was seeded;
- failure class;
- expected verdict;
- target contract version if relevant;
- canonical digest or stable identifier;
- known limits.

The proof path should use frozen in-memory records. Do not make `prove` depend on writing or reading local files.

## Required proof shape for future exploratory harnesses

A real exploratory harness still needs:

1. known-good behavior;
2. planted-bad behavior or mutant;
3. a nonempty frozen corpus or finite model;
4. a pure deterministic `prove(impl) -> bool`;
5. paired `unittest` coverage;
6. `--self-test` that cannot pass inert;
7. honest known limits.

Generated cases alone are not proof. The test must show that an intentionally bad implementation is caught.

## Candidate PR sequence

### PR 88 - combinatorial/t-way coverage harness

Candidate files:

- `harnesses/core/combinatorial_coverage_test_harness.py`
- `tests/core/test_combinatorial_coverage_test_harness.py`

Proof target:

- correct generator covers all required 2-way interactions for a small finite parameter model;
- planted mutants omit pairs, collapse distinct values, ignore parameters, or report coverage without checking all required pairs.

### PR 89 - counterexample replay harness

Candidate files:

- `harnesses/core/counterexample_replay_test_harness.py`
- `tests/core/test_counterexample_replay_test_harness.py`

Proof target:

- correct freezer canonicalizes failure records into stable replay fixtures;
- planted mutants omit seed/path/failure metadata, accept empty failure class, or produce unstable digests.

### PR 90 - stateful sequence budget harness

Candidate files:

- `harnesses/core/stateful_sequence_budget_test_harness.py`
- `tests/core/test_stateful_sequence_budget_test_harness.py`

Proof target:

- correct explorer bounds action sequences and records visited state/action paths;
- planted mutants loop forever, ignore terminal states, skip visited tracking, or treat action names as proof.

### PR 91 - boundary corpus expander harness

Candidate files:

- `harnesses/core/boundary_corpus_expander_test_harness.py`
- `tests/core/test_boundary_corpus_expander_test_harness.py`

Proof target:

- correct expander preserves planted-bad fixtures while adding deterministic boundary cases;
- planted mutants delete required cases, add random-only cases, miss boundary classes, invent requirements, or count duplicates as coverage.

### PR 92 - closeout docs and mapping pass

Update inventory, roadmap, harness map, reviewer docs, and learnings after the exploratory harnesses settle. Avoid full replacement of large Markdown files from truncated connector reads.

## Stop or split triggers

Stop or split the PR if:

- `prove` needs filesystem, clock, network, subprocess, threads, or unseeded randomness;
- the harness needs a runtime dependency;
- the test still passes after removing the load-bearing assertion;
- the mutant is caught for the wrong reason;
- generated inputs replace planted-bad fixtures;
- a helper starts becoming a broad framework instead of a compact proof pattern;
- docs require large full-file replacement from a truncated connector read;
- the change touches workflows, dependency files, dashboard dependencies, branch protection, repo settings, or generated status files.

## Source check

Current external sources used as evidence, not instruction:

- Python `unittest` documentation: `https://docs.python.org/3/library/unittest.html`
- Hypothesis documentation: `https://hypothesis.readthedocs.io/en/latest/`
- Hypothesis stateful testing documentation: `https://hypothesis.readthedocs.io/en/latest/stateful.html`
- NIST combinatorial testing project: `https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software`
- Google Atheris README: `https://github.com/google/atheris`

Repo-local rules and current operator instruction remain authority.
