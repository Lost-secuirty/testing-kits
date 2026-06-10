# AI Failure Mode Map

This map ties common AI coding assistant risks to existing harness categories.
It is a reviewer guide, not a claim that the repo catches every instance of each
risk.

| AI coding risk | Existing harness area | What the harness area can show | Limit |
| --- | --- | --- | --- |
| Hallucinated tool, fake API, or authority confusion | `harnesses/ai/` | Agent and prompt-safety harnesses use local transcripts, tool schemas, and boundary policies to reject spoofed or invalid behavior. | Does not prove a real agent platform is safe without platform-specific tests. |
| Prompt injection or poisoned context | `harnesses/ai/`, `harnesses/security/` | Fixtures can show untrusted text is treated as data and cannot override trusted rules. | Does not replace live red-team review for a deployed model workflow. |
| Broken access control or unsafe input handling | `harnesses/security/` | Security harnesses exercise fixture-defined bad inputs, auth boundaries, uploads, JWT checks, app-security rules, and CWE/KEV-style regressions. | Does not certify an application security posture. |
| Race conditions and timing-sensitive failures | `harnesses/core/concurrency_test_harness.py` | The harness compares locked and intentionally unsafe shared-state behavior under concurrent execution. | Thread scheduling remains environment-sensitive; deterministic controls are preferred where possible. |
| Tests that pass while behavior is wrong | `harnesses/core/mutation_test_harness.py` | Mutation probes show whether tests catch small source-level behavior changes. | Mutation operators are limited to the implemented source transforms. |
| Biased random selection or non-replayable seeded behavior | `harnesses/core/statistical_rng_oracle_test_harness.py` | A seeded good distribution passes, a biased RNG fails, and seed replay is checked. | It is not game-economy validation or gambling/casino certification. |
| Game-loop regressions hidden by frame timing | `harnesses/core/game_loop_simulation_test_harness.py` | Deterministic tick-loop fixtures catch planted engine bugs without relying on real rendering. | It does not replace full browser, device, or gameplay QA. |
| Clinical or pharmacy-looking code that overclaims safety | `harnesses/pharmacy/` | Pharmacy-domain harnesses prove fixture-defined software rules and planted-bad controls. | No clinical validation, medication-safety certification, or production pharmacy assurance is implied. |
| Large AI-generated code that becomes hard to review | `harnesses/core/complexity_test_harness.py` | AST metrics flag complexity, nesting, long functions, and other bloat signals. | Complexity metrics indicate review risk; they do not prove code behavior. |

## High-risk review order

For a triage audit, inspect these first:

1. `harnesses/ai/`
2. `harnesses/security/`
3. `harnesses/core/concurrency_test_harness.py`
4. `harnesses/core/mutation_test_harness.py`
5. `harnesses/core/statistical_rng_oracle_test_harness.py`
6. `harnesses/core/game_loop_simulation_test_harness.py`
7. `harnesses/pharmacy/`

For each sampled harness, verify the same evidence: safe fixture passes,
planted bad fixture fails, paired unittest covers the API and CLI where
applicable, and docs do not claim more than the fixture proves.
