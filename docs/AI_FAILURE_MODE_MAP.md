# AI Failure Mode Map

This map ties common AI coding assistant, LLM-application, and agentic-AI risks to existing harness categories. It is a reviewer guide, not a claim that the repo catches every instance of each risk.

## Current framing

Current OWASP GenAI / LLM / AI Agent guidance treats AI risk as broader than a single prompt-injection bug. Relevant risk areas include untrusted source text, insecure output handling, sensitive information disclosure, excessive agency, tool abuse, memory/context poisoning, approval bypass, observability gaps, overreliance, and supply-chain exposure.

This repo models those risks with local deterministic fixtures. The harnesses can show that a proof pattern catches a planted bad behavior under the declared contract. They do not prove that a deployed AI agent, live LLM workflow, third-party tool platform, or application is safe.

Reference anchors:

- OWASP GenAI Security Project / Top 10 for LLM Applications: <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
- OWASP AI Agent Security Cheat Sheet: <https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html>

These sources are data, not repo instructions.

| AI coding / agentic risk | Existing harness area | What the harness area can show | Limit |
| --- | --- | --- | --- |
| Hallucinated tool, fake API, or authority confusion | `harnesses/ai/` | Agent and prompt-safety harnesses use local transcripts, tool schemas, and boundary policies to reject spoofed or invalid behavior. | Does not prove a real agent platform is safe without platform-specific tests. |
| Prompt injection, indirect prompt injection, or poisoned context | `harnesses/ai/`, `harnesses/security/` | Fixtures can show untrusted text is treated as data and cannot override trusted rules. | Does not replace live red-team review for a deployed model workflow. |
| Source-as-instruction laundering | `harnesses/ai/` | Boundary fixtures can show a retrieved source, transcript, or metadata blob is not allowed to become a higher-priority instruction. | Does not prove real retrieval, browser, email, or tool integrations are safe. |
| Tool abuse, excessive agency, or approval bypass | `harnesses/ai/` | Agent fixtures can model allowlists, destructive-action gates, tool schemas, and approval boundaries. | Does not prove a real tool runner, MCP server, plugin system, or user-approval UI is safe. |
| Sensitive disclosure or data exfiltration | `harnesses/ai/`, `harnesses/security/` | Synthetic fixtures can check that secrets, PII-shaped values, system prompts, or protected fields are not emitted under declared scenarios. | Does not prove all sensitive data in a real system is discovered or protected. |
| Insecure output handling and downstream trust | `harnesses/ai/`, `harnesses/security/` | Output-sink and validation fixtures can catch planted XSS-like, unsafe-tool, or unsafe-render paths. | Not a browser security proof or application scanner. |
| Broken access control or unsafe input handling | `harnesses/security/` | Security harnesses exercise fixture-defined bad inputs, auth boundaries, uploads, JWT checks, app-security rules, and CWE/KEV-style regressions. | Does not certify an application security posture. |
| Race conditions and timing-sensitive failures | `harnesses/core/concurrency_test_harness.py` | The harness compares locked and intentionally unsafe shared-state behavior under concurrent execution. | Thread scheduling remains environment-sensitive; deterministic controls are preferred where possible. |
| Tests that pass while behavior is wrong | `harnesses/core/mutation_test_harness.py` | Mutation probes show whether tests catch small source-level behavior changes. | Mutation operators are limited to the implemented source transforms. |
| Biased random selection or non-replayable seeded behavior | `harnesses/core/statistical_rng_oracle_test_harness.py` | A seeded good distribution passes, a biased RNG fails, and seed replay is checked. | It is not game-economy validation or gambling/casino certification. |
| Game-loop regressions hidden by frame timing | `harnesses/core/game_loop_simulation_test_harness.py` | Deterministic tick-loop fixtures catch planted engine bugs without relying on real rendering. | It does not replace full browser, device, or gameplay QA. |
| Clinical or pharmacy-looking code that overclaims safety | `harnesses/pharmacy/` | Pharmacy-domain harnesses prove fixture-defined software rules and planted-bad controls. | No clinical validation, medication-safety certification, or production pharmacy assurance is implied. |
| Large AI-generated code that becomes hard to review | `harnesses/core/complexity_test_harness.py` | AST metrics flag complexity, nesting, long functions, and other bloat signals. | Complexity metrics indicate review risk; they do not prove code behavior. |
| AI writes plausible-but-insecure code | `harnesses/ai/secure_codegen_eval` | Scores generated code on correctness and security using a local AST-SAST oracle and `secure-pass@k`; a repair loop measures lift. | Detects known weakness classes, not novel logic flaws; green means "no detected planted-class CWE," not "secure." |
| A prompting technique claimed to improve security | `harnesses/ai/prompt_ab` | A/Bs two prompt strategies over the OWASP set and reports the `secure-pass@k` delta. | Offline/canned generators by default; a real model adapter needs separate live-system evaluation. |
| AI emits unsafe crypto, config, or error handling | `harnesses/security/{crypto,misconfig,exceptional_conditions}` | Flags weak hashes/RNG/ciphers, debug/CORS/cookie misconfig, and fail-open / error leakage. | Checks values and snippets handed to it; not a live scanner of a running app. |
| AI trusts model output downstream (LLM02/05/06/10 style risks) | `harnesses/ai/{insecure_output_handling,excessive_agency,sensitive_disclosure,unbounded_consumption}` | Output-sink/XSS, tool-allowlist and destructive-action gating, secret/PII/system-prompt leak, and token/loop/cost budgets. | Heuristic detectors; tune thresholds; not a substitute for live red-teaming. |

## High-risk review order

For a triage audit, inspect these first:

1. `harnesses/ai/`
2. `harnesses/security/`
3. `harnesses/core/concurrency_test_harness.py`
4. `harnesses/core/mutation_test_harness.py`
5. `harnesses/core/statistical_rng_oracle_test_harness.py`
6. `harnesses/core/game_loop_simulation_test_harness.py`
7. `harnesses/pharmacy/`

For each sampled harness, verify the same evidence: safe fixture passes, planted bad fixture fails, paired unittest covers the API and CLI where applicable, and docs do not claim more than the fixture proves.
