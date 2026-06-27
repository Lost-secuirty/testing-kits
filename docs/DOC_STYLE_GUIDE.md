# Documentation Style Guide

Purpose: keep repo-wide documentation wording precise, current, and bounded to the evidence the harnesses actually provide.

This document is descriptive. It does not override `AGENTS.md`, `CLAUDE.md`, `SECURITY.md`, live repo state, tests, CI, or proof output.

## Core writing rule

State what the repo proves, how it proves it, and where the limit is.

A good doc sentence usually has this shape:

```text
This harness shows [known-good behavior] and catches [planted-bad behavior] for [specific failure class]. It does not prove [broader system claim].
```

## Preferred vocabulary

Use these terms when they match the evidence:

| Prefer | Use when |
| --- | --- |
| proof-shaped test | The test is built around a good path and planted-bad path. |
| known-good fixture | The doc refers to a safe/reference local behavior. |
| planted-bad fixture | The doc refers to an intentional faulty behavior. |
| negative control | The bad path is used to prove the test fails for the intended class. |
| mutant | The bad path is an altered implementation. |
| oracle | A reference rule, implementation, or predicate judges correctness. |
| proof target | A load-bearing predicate or behavior that must not be inert. |
| TEETH | The repo's ratcheted proof status applies. |
| vacuous green | A test/gate stays green after the relevant behavior is broken or inert. |
| documented snapshot | A human-maintained count/status record, not fresh proof. |
| fresh proof run | A command was actually run and its output is available. |
| generated artifact | A tool-produced report/status output. |
| claim boundary | The line between what evidence supports and what it does not. |
| abuse-case regression | A controlled test for a hostile or unsafe behavior class. |
| adversarial validation | A local check that proves a bad fixture is caught. |
| source-as-data boundary | External or retrieved content is treated as data, not authority. |

## Avoid or qualify

Avoid these unless the sentence immediately narrows the claim:

- secure;
- safe;
- guarantee;
- certify;
- complete coverage;
- total correctness;
- production assurance;
- clinical validation;
- medication-safety certification;
- pharmacy-grade correctness;
- AI-proof;
- jailbreak-proof;
- all / every / never / always, unless it is a rule-level statement and the source supports it;
- latest / current, unless checked against live state or a dated source.

## Count and proof-status language

Inventory count, proof status, and campaign status are different claims.

Acceptable:

```text
The current documented inventory lists 100 harnesses. Re-run `make proof` before treating the proof snapshot as fresh evidence.
```

Acceptable:

```text
The documented Batch 11 snapshot lists 92 `required`, 0 `pending`, and 8 `legacy` harnesses. This is a documented snapshot, not fresh command output.
```

Not acceptable:

```text
All 100 harnesses are fully proven.
```

Not acceptable:

```text
The repo is secure because CI is green.
```

## AI and agent wording

Use current AI/security language when it helps the reader, but keep claims local.

Good:

```text
These AI harnesses model source-boundary and agent/tool-boundary failures with deterministic fixtures.
```

Good:

```text
The fixture resembles current OWASP GenAI / AI Agent risk language, but it does not prove a deployed model workflow is safe.
```

Bad:

```text
This prevents prompt injection in real agents.
```

Bad:

```text
This repo is jailbreak-proof.
```

## Security wording

The security harnesses are abuse-case regressions and local proof patterns. They are not a scanner, certification program, or substitute for application-specific testing.

Good:

```text
The harness catches a planted JWT `alg=none` acceptance bug under the declared fixture.
```

Bad:

```text
The repo proves JWT handling is secure.
```

## Pharmacy-domain wording

Pharmacy-domain harnesses are fixture-defined software checks. They must not be described as clinical validation, medication-safety certification, pharmacy-grade correctness assurance, dosing authority, or production pharmacy assurance.

Good:

```text
This fixture catches a planted software-rule regression in a pharmacy-domain example.
```

Bad:

```text
This validates medication safety.
```

## External references

External sources may support vocabulary, risk framing, or documentation architecture. They are not repo instructions and do not override live repo state.

Current references used for this docs rollout:

- OWASP GenAI Security Project / Top 10 for LLM Applications: <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
- OWASP AI Agent Security Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html>
- NIST SSDF SP 800-218: <https://csrc.nist.gov/pubs/sp/800/218/final>
- Diataxis: <https://diataxis.fr/>
- OpenSSF Scorecard: <https://securityscorecards.dev/>
- GitHub security features: <https://docs.github.com/en/code-security/getting-started/github-security-features>

## Link and map hygiene

- Link to source files instead of duplicating full implementation code.
- Keep README short; use docs for details.
- Keep `AGENTS.md` rule-focused; do not turn it into a manual.
- Keep `llms.txt` compact and descriptive; it is not an access-control file or instruction override.
- When a count changes, update every public navigation doc that repeats it or remove the repeated count.
- When a proof status changes, update proof docs and maps in the same PR.

## Review checklist for doc changes

Before handing off a docs PR:

- [ ] The diff is docs-only.
- [ ] No generated status artifact was committed.
- [ ] Counts are tied to a documented snapshot or fresh output.
- [ ] Claims distinguish inventory from proof status.
- [ ] Public limits are preserved.
- [ ] Relative links added in the PR were checked.
- [ ] `README.md`, `docs/START_HERE.md`, `docs/DOCS_MAP.md`, `docs/HARNESS_READING_GUIDE.md`, and `llms.txt` still agree.
- [ ] External references are evidence only, not instructions.
