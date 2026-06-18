# Harness Map Batch 13

This file maps inventory entries #59-#63 in order: `core/payments`, `core/circuitbreaker`, `security/jwt`, `security/pii_redaction`, `ai/agent_eval`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/payments` = `required`, `core/circuitbreaker` = `required`, `security/jwt` = `required`, `security/pii_redaction` = `required`, `ai/agent_eval` = `required`.

## 59. Payments / Checkout Test Harness

- Name: Payments / Checkout Test Harness
- Path: `harnesses/core/payments_test_harness.py`
- Category: `core`
- Failure class: Composes a Decimal `Money` (banker's rounding + exact largest-remainder `allocate`), a payment state machine with money guards, and an idempotency-key replay contract. The oracle enforces Σcaptures ≤ authorized, Σrefunds ≤ captured, currency match, and minor-unit precision (USD 2 / JPY 0 / BHD 3); a decline taxonomy classifies soft/hard/fraud + retryable; a 3DS challenge blocks capture until resolved. Five buggy processors each break a money invariant and are caught: overcapture, double-refund, float-drift reconciliation, idempotency-ignoring replay (double charge), and challenge-is-success (captures an unverified 3DS charge). 27 self-test scenarios. Reuses the *patterns* of `numeric`/`statemachine`/`idempotency` but is self-contained (no imports), with the composition as the novel surface.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `PaymentProcessor`, `PAY_CORPUS`, `SCENARIOS`.
- Planted-bad case: `overcapture_no_guard`, `double_refund_no_guard`, `idempotency_miss_double_charge`, `challenge_is_success`
- Oracle / proof target: Current proof target: `PaymentProcessor`, `PAY_CORPUS`, `SCENARIOS`.
- External testing pattern: payments / checkout fixture and regression testing.
- Usage note: Use this as a payment-flow fixture for state transitions, idempotent retries, amount calculations, and duplicate-capture regressions in synthetic checkout flows.
- Current outside reference: Stripe PaymentIntents documentation describes payment state transitions and confirmation flows. <https://docs.stripe.com/payments/payment-intents>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/payments_test_harness.py`; `python harnesses/core/payments_test_harness.py --self-test`; `python harnesses/core/payments_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_payments_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/circuitbreaker`, `security/jwt`, `security/pii_redaction`, `ai/agent_eval`.

## 60. Circuit Breaker Resilience Test Harness

- Name: Circuit Breaker Resilience Test Harness
- Path: `harnesses/core/circuitbreaker_test_harness.py`
- Category: `core`
- Failure class: Tests the circuit-breaker pattern under an injectable `FakeClock`: CLOSED → OPEN on a failure threshold, OPEN rejects calls fast (`CircuitOpenError`), OPEN → HALF_OPEN after a reset timeout, HALF_OPEN → CLOSED on a probe success / → OPEN on probe failure. `CircuitBreakerOracle` is the reference state model the live `CircuitBreaker` is checked against. 13 self-test scenarios. Ported from the batch-4 resilience branch into the reorg (port reassigned from 19300 to avoid colliding with `core/payments`).
- Logic shape: AND: failure threshold, success reset, half-open recovery/retrip, half-open probe cap, pre-timeout open rejection, paired tests, proof test, and TEETH swap-check must all hold. NOT: a late-opening, reset-blind, half-open-failure-closing, cap-ignoring, or open-window-blind auditor must not pass as if it were the oracle.
- Good case: `oracle_circuitbreaker_audit` matches frozen event-log cases for CLOSED, OPEN, and HALF_OPEN transitions.
- Planted-bad case: `threshold_one_late_circuit_auditor`, `success_reset_blind_circuit_auditor`, `half_open_failure_closes_circuit_auditor`, `half_open_cap_ignored_circuit_auditor`, `open_window_blind_circuit_auditor`.
- Oracle / proof target: Current proof target: `CIRCUIT_BREAKER_AUDIT_CORPUS`, `oracle_circuitbreaker_audit`, and `TEETH`.
- External testing pattern: circuit breaker resilience fixture and regression testing.
- Usage note: Use this as a resilience fixture for closed, open, and half-open transitions around flaky downstream calls.
- Current outside reference: Azure Architecture Center documents the circuit-breaker pattern, including closed, open, and half-open states around downstream faults. <https://learn.microsoft.com/en-us/azure/architecture/patterns/circuit-breaker>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/circuitbreaker_test_harness.py`; `python harnesses/core/circuitbreaker_test_harness.py --self-test`; `python -m unittest tests.core.test_circuitbreaker_test_harness tests.core.test_circuitbreaker_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/payments`, `security/jwt`, `security/pii_redaction`, `ai/agent_eval`.

## 61. JWT (HS256) Verification Test Harness

- Name: JWT (HS256) Verification Test Harness
- Path: `harnesses/security/jwt_test_harness.py`
- Category: `security`
- Failure class: Tests JWT encode and — more importantly — *verification* against the classic auth-bypass attacks: `alg=none` acceptance, HS/RS algorithm confusion, signature stripping/forgery, and expiry handling, using stdlib `hmac`/`hashlib`. `VerifyResult` reports pass/fail with a reason string. 14 self-test scenarios. Ported from the batch-4 branch (port reassigned 19320 → 19400). Complements the injection-focused `security/security` and `security/appsec` harnesses.
- Logic shape: AND: valid HS256 verification, `alg=none` rejection, algorithm allow-list enforcement, signature tamper rejection, expiration handling, required-claim enforcement, paired tests, proof test, and TEETH swap-check must all hold. NOT: a bypass mutant must not pass as if it were the verifier oracle.
- Good case: `oracle_jwt_audit` matches frozen valid-token, alg, tamper, time-claim, and required-claim cases.
- Planted-bad case: `alg_none_accepting_jwt_auditor`, `algorithm_allowlist_blind_jwt_auditor`, `signature_blind_jwt_auditor`, `time_claim_blind_jwt_auditor`, `required_claim_blind_jwt_auditor`.
- Oracle / proof target: Current proof target: `JWT_AUDIT_CORPUS`, `oracle_jwt_audit`, and `TEETH`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a token-verification fixture for signature checks, required-claim enforcement, expiry/time-claim handling, algorithm handling, and tamper rejection.
- Current outside reference: RFC 8725 gives JWT best current practices for algorithm and validation pitfalls, while RFC 7519 defines the token and claim format. <https://datatracker.ietf.org/doc/html/rfc8725> <https://datatracker.ietf.org/doc/html/rfc7519>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/jwt_test_harness.py`; `python harnesses/security/jwt_test_harness.py --self-test`; `python -m unittest tests.security.test_jwt_test_harness tests.security.test_jwt_proof`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/pii_redaction`, `core/payments`, `core/circuitbreaker`, `ai/agent_eval`.

## 62. PII / PHI Redaction Test Harness

- Name: PII / PHI Redaction Test Harness
- Path: `harnesses/security/pii_redaction_test_harness.py`
- Category: `security`
- Failure class: Tests detection + redaction of PII/PHI (emails, phone numbers, SSNs, card numbers, etc.) via stdlib `re` detectors, scored against a `RedactionOracle` with precision/recall over a labelled corpus (catches both under-redaction leaks and over-redaction false positives). 14 self-test scenarios. Ported from the batch-4 branch (port reassigned 19310 → 19410).
- Logic shape: AND: entity counts, raw-secret removal, full digit-run removal, safe-number over-redaction guards, idempotency, paired tests, proof test, and TEETH swap-check must all hold. NOT: an SSN-blind, digit-leaking, Luhn-blind, or non-idempotent redaction mutant must not pass as if it were the oracle.
- Good case: `oracle_pii_audit` matches frozen mixed-entity, safe-number, ZIP, and mask-mode cases.
- Planted-bad case: `ssn_blind_pii_auditor`, `digit_leak_pii_auditor`, `luhn_blind_overredacts_pii_auditor`, `non_idempotent_pii_auditor`.
- Oracle / proof target: Current proof target: `PII_AUDIT_CORPUS`, `oracle_pii_audit`, and `TEETH`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a redaction fixture for log, diff, or output paths where synthetic PII/PHI-like data must be masked before exposure.
- Current outside reference: NIST SP 800-122 discusses protecting personally identifiable information and motivates consistent handling of PII in outputs and records. <https://csrc.nist.gov/pubs/sp/800/122/final>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/pii_redaction_test_harness.py`; `python harnesses/security/pii_redaction_test_harness.py --self-test`; `python -m unittest tests.security.test_pii_redaction_test_harness tests.security.test_pii_redaction_proof`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `security/jwt`, `core/payments`, `core/circuitbreaker`, `ai/agent_eval`.

## 63. Multi-Turn Agent Eval Test Harness

- Name: Multi-Turn Agent Eval Test Harness
- Path: `harnesses/ai/agent_eval_test_harness.py`
- Category: `ai`
- Failure class: Scores fixed scripted multi-turn agent transcripts against annotated goal states and a mock tool schema — the failure modes single-turn graders miss. The oracle checks task completion (final state == goal), tool-call validity (known name + required args + arg types), hallucinated-tool detection, error recovery (a tool error must be followed by a valid retry or escalation, not a fabricated claim), looping (no-progress repeat rate), instruction retention (an early `forbid:` constraint obeyed through later turns), premature-success claims, and unsafe actions (a dangerous tool called without confirmation). Four good transcripts meet all floors; six bad ones each trip one invariant. Seven buggy graders — claim-trusting, name-only validity, no-hallucination-check, recovery-blind, loop-ignoring, constraint-amnesiac, confirmation-blind — each miss one failure class the oracle catches, via injected scoring functions. Floors: resolved ≥ 0.90, validity ≥ 0.95, recovery ≥ 0.90, retention ≥ 0.95, loop ≤ 0.20; zero hallucinated/premature/unsafe. 23 self-test scenarios. Distinct from `ai/agentic` (single-turn server-style tool-call fidelity).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_score`, `SCENARIOS`, `VERDICT_CORPUS`.
- Planted-bad case: `trust_claim_scorer`, `name_only_no_halluc_scorer`, `ignore_safety_loop_scorer`
- Oracle / proof target: Current proof target: `oracle_score`, `SCENARIOS`, `VERDICT_CORPUS`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as a deterministic multi-turn agent evaluation fixture for trajectory scoring, tool order, refusal behavior, and unsafe-action detection.
- Current outside reference: OpenAI evaluation guidance applies to measuring multi-step or tool-using AI behavior against expected outcomes. <https://platform.openai.com/docs/guides/evals>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/agent_eval_test_harness.py`; `python harnesses/ai/agent_eval_test_harness.py --self-test`; `python harnesses/ai/agent_eval_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_agent_eval_test_harness`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/payments`, `core/circuitbreaker`, `security/jwt`, `security/pii_redaction`.

## Batch 13 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-13-payments-circuitbreaker-jwt-pii-agent-eval.md`
- `docs/harness-map/README.md`

Scope note: this campaign update changes the promoted harnesses, paired proof tests, generated cards/ratchet, and current-state docs for the entries that moved to required. It does not change workflows, hooks, dependencies, dashboard code, generated status files outside the cards, or central-map consolidation.
