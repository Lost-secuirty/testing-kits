# Combinatorial coverage batch 1 web/source check

Status: evidence-only source note for PR #88.
Date: 2026-06-27.
Repo: `Lost-secuirty/testing-kits`.

This source note supports the first exploratory proof-layer harness batch. It is evidence only. Repo-local rules, live CI, and operator instruction remain authority.

## Sources checked

- Python `itertools` documentation: `https://docs.python.org/3/library/itertools.html`
  - Relevant evidence: the standard library includes combinatoric iterators such as `product()` for Cartesian products and `combinations()` for fixed-length combinations.
- Python `unittest` documentation: `https://docs.python.org/3/library/unittest.html`
  - Relevant evidence: `unittest` remains the repo-native test framework for paired harness tests.
- NIST combinatorial testing project: `https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software`
  - Relevant evidence: combinatorial methods are a recognized software-testing approach for interaction coverage.

## Repo-local design decision

The PR uses only Python standard-library primitives:

- `itertools.combinations`;
- `itertools.product`;
- `dataclasses`;
- `unittest`;
- existing pure-stdlib `harnesses._teeth`.

No Hypothesis, Atheris, pytest, coverage.py, ACTS, or other dependency is added.

## Claim boundary

This batch does not claim exhaustive real-application testing.

It claims only that a small deterministic harness can independently audit whether a declared finite parameter model has all required pairwise interactions covered, and that planted coverage mutants are caught.
