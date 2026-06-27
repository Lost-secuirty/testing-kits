# Harness Map Batch 5

This file maps inventory entries #21-#25 in order: `core/idempotency`, `core/statemachine`, `core/numeric`, `security/authz`, `ai/llm_eval`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/idempotency` = `required`, `core/statemachine` = `required`, `core/numeric` = `required`, `security/authz` = `required`, `ai/llm_eval` = `required`.

## 21. Idempotency / Retry-Safety Test Harness

- Name: Idempotency / Retry-Safety Test Harness
- Path: `harnesses/core/idempotency_test_harness.py`
- Category: `core`
- Failure class: Tests retry-safety: idempotency keys, an atomic check-and-set dedup store (PENDING/COMPLETED/FAILED + TTL + persisted response artifact), retry convergence (replay returns identical cached response, side-effect counter does not advance), concurrent duplicate suppression via `threading.Barrier` (exactly-once execution), and the “state-only store loses the response” failure mode. Classifies idempotent vs non-idempotent HTTP methods.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `IdempotencyStore`, `_TEETH_CASES`.
- Planted-bad case: `response_not_persisted`
- Oracle / proof target: Current proof target: `IdempotencyStore`, `_TEETH_CASES`.
- External testing pattern: idempotency / retry-safety fixture and regression testing.
- Current outside reference: Stripe documents idempotent requests as using an idempotency key so retried requests can return the same result instead of duplicating side effects. <https://docs.stripe.com/api/idempotent_requests>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/idempotency_test_harness.py`; `python -m unittest tests.core.test_idempotency_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/statemachine`, `core/numeric`.

## 22. State Machine Test Harness

- Name: State Machine Test Harness
- Path: `harnesses/core/statemachine_test_harness.py`
- Category: `core`
- Failure class: Validates finite-state-machine correctness with a generic `StateMachine` (states, initial, `Transition` rules, terminal set) that raises `InvalidTransition` and leaves state unchanged on rejection. Drives an order-lifecycle example (CREATED→PAID→SHIPPED→DELIVERED, CANCELLED, terminal states) plus fixture machines (orphaned-state, cyclic, acyclic, non-deterministic) to exercise reachability/dead-state detection, cycle detection, transition-coverage tracking, and determinism checking.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `check_fsm`, `CHECKER_CORPUS`.
- Planted-bad case: `nondeterminism_blind`, `assume_all_reachable`, `undirected_reachability`
- Oracle / proof target: Current proof target: `check_fsm`, `CHECKER_CORPUS`.
- External testing pattern: state machine fixture and regression testing.
- Current outside reference: W3C SCXML documents state-machine notation for states, events, transitions, and executable state logic. <https://www.w3.org/TR/scxml/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/statemachine_test_harness.py`; `python harnesses/core/statemachine_test_harness.py --self-test`; `python harnesses/core/statemachine_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_statemachine_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/idempotency`, `core/numeric`.

## 23. Numeric / Money Precision Test Harness

- Name: Numeric / Money Precision Test Harness
- Path: `harnesses/core/numeric_test_harness.py`
- Category: `core`
- Failure class: Demonstrates and guards against silent numeric bugs. A `Money` helper on `decimal.Decimal` (configurable rounding, default banker’s ROUND_HALF_EVEN, largest-remainder allocation) is the correct reference against which naive float math is shown to fail: 0.1+0.2≠0.3, accumulation drift, wrong-rounding, big+small precision loss (1e16+1==1e16), float overflow to inf, NaN comparison oddities, and `Fraction` exactness. Bill-splitting allocations sum back to the exact total with no lost cent.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_allocate`, `ALLOC_CORPUS`.
- Planted-bad case: `float_accumulation_drift`, `truncate_no_remainder`, `half_up_overallocates`
- Oracle / proof target: Current proof target: `oracle_allocate`, `ALLOC_CORPUS`.
- External testing pattern: numeric / money precision fixture and regression testing.
- Current outside reference: Python `decimal` documents exact decimal arithmetic and configurable rounding for financial-style calculations. <https://docs.python.org/3/library/decimal.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/numeric_test_harness.py`; `python harnesses/core/numeric_test_harness.py --self-test`; `python harnesses/core/numeric_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_numeric_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/idempotency`, `core/statemachine`.

## 24. Authorization / Access-Control Test Harness

- Name: Authorization / Access-Control Test Harness
- Path: `harnesses/security/authz_test_harness.py`
- Category: `security`
- Failure class: Focuses on authorization correctness (OWASP #1, distinct from the injection-focused Security harness). A `Role` enum (ANONYMOUS/USER/EDITOR/ADMIN) and `AccessControl` engine combine RBAC grants with per-resource ownership checks, deny-by-default, and revocation-overrides-grant. Asserts the full role×action matrix, vertical privilege escalation denials, horizontal/IDOR denials (user A cannot touch user B’s object), least-privilege defaulting on forged/missing role claims, and token-scope defense-in-depth. The mock server enforces identical rules over HTTP via a `Bearer id:role:scopes` token, returning 200/401/403/404.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_decide`, `AUTHZ_CORPUS`, `EXPECTED_DECISIONS`.
- Planted-bad case: `default_allow`, `deny_precedence_ignored`, `ownership_over_grant`
- Oracle / proof target: Current proof target: `oracle_decide`, `AUTHZ_CORPUS`, `EXPECTED_DECISIONS`.
- External testing pattern: security regression and control-fixture testing.
- Current outside reference: OWASP WSTG authorization testing covers privilege escalation, insecure direct object references, and access-control bypass checks. <https://owasp.org/www-project-web-security-testing-guide/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/authz_test_harness.py`; `python harnesses/security/authz_test_harness.py --self-test`; `python harnesses/security/authz_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_authz_test_harness`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/idempotency`, `core/statemachine`, `core/numeric`, `ai/llm_eval`.

## 25. LLM / AI-Feature Eval Test Harness

- Name: LLM / AI-Feature Eval Test Harness
- Path: `harnesses/ai/llm_eval_test_harness.py`
- Category: `ai`
- Failure class: Tests LLM-backed features without calling any real model. A seeded `MockLLM` is deterministic at temperature 0 (reproducible) and perturbs output via HMAC at temperature > 0 (controlled non-determinism), and refuses dangerous prompts. Four graders score outputs by semantic equivalence rather than byte-exact match: ExactMatch, SemanticOverlap (token-set Jaccard), RegexFormat, and a deterministic JudgeStub (LLM-as-judge). A ConsistencyChecker asserts pass-rate over N samples under temperature; an InjectionTester runs a 15-item adversarial corpus (ignore-previous-instructions, system-prompt exfiltration, delimiter/role-play jailbreaks) against a rule-based guardrail; a RefusalChecker verifies safety refusals. An EvalSuite produces an aggregate EvalReport with per-case transcripts.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_grade`, `GRADE_CORPUS`.
- Planted-bad case: `jaccard_union_swap`, `ge_to_gt`, `no_lowercase`
- Oracle / proof target: Current proof target: `oracle_grade`, `GRADE_CORPUS`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Current outside reference: OpenAI evaluation guidance frames evals as tests for measuring model or application behavior against expected criteria. <https://platform.openai.com/docs/guides/evals>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/llm_eval_test_harness.py`; `python harnesses/ai/llm_eval_test_harness.py --self-test`; `python harnesses/ai/llm_eval_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_llm_eval_test_harness`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/idempotency`, `core/statemachine`, `core/numeric`, `security/authz`.

## Batch 5 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-05-idempotency-statemachine-numeric-authz-llm-eval.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
