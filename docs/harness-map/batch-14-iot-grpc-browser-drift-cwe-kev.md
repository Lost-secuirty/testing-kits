# Harness Map Batch 14

This file maps inventory entries #64-#68 in order: `core/iot_telemetry`, `core/grpc_contract`, `core/browser_e2e`, `ai/drift_detection`, `security/cwe_kev_regression`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/iot_telemetry` = `required`, `core/grpc_contract` = `required`, `core/browser_e2e` = `required`, `ai/drift_detection` = `required`, `security/cwe_kev_regression` = `required`.

## 64. IoT / Telemetry Ingest Test Harness

- Name: IoT / Telemetry Ingest Test Harness
- Path: `harnesses/core/iot_telemetry_test_harness.py`
- Category: `core`
- Failure class: Models an MQTT-like telemetry ingest path as pure data with an injectable `FakeClock` for server-ingest time. The oracle `ingest()` enforces QoS semantics, per-topic re-sequencing by `seq`, idempotency-key dedupe, clock-skew handling, watermark/allowed-lateness windowing, retained-latest-only, persistent-session replay, and last-will behavior. The current TEETH proof also uses an aggregate fingerprint over the frozen stream and reading set, so planted-bad cases include ingest/order defects plus `float_mean_drift`, which catches binary-float drift in the rolled reading mean.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_aggregate`, `SCENARIOS`.
- Planted-bad case: `accept_late_window`, `no_resequence`, `no_qos2_dedupe`, `float_mean_drift`
- Oracle / proof target: Current proof target: `oracle_aggregate`, `SCENARIOS`.
- External testing pattern: iot / telemetry ingest fixture and regression testing.
- Usage note: Use this as an ingest fixture for telemetry decoding, ordering, duplicate messages, time windows, and invalid sensor payloads.
- Current outside reference: MQTT 5.0 is a standard publish/subscribe protocol commonly used in IoT telemetry. <https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/iot_telemetry_test_harness.py`; `python harnesses/core/iot_telemetry_test_harness.py --self-test`; `python harnesses/core/iot_telemetry_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_iot_telemetry_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/grpc_contract`, `core/browser_e2e`.

## 65. gRPC / Proto Contract Test Harness

- Name: gRPC / Proto Contract Test Harness
- Path: `harnesses/core/grpc_contract_test_harness.py`
- Category: `core`
- Failure class: Models protos and a mock gRPC service as pure data (no grpc/protobuf libs) and audits the contract rules LLM-written glue breaks. The oracle enforces proto-evolution safety (reserve removed field numbers, no number reuse, no wire-type change on kept fields), open-vs-closed enum unknown-value handling, deadline propagation (downstream ≤ original − elapsed, ±5 ms via a `MsClock`), streaming half-close (handler stops emitting after CloseSend), status-code correctness (RESOURCE_EXHAUSTED vs PERMISSION_DENIED across the 17 canonical codes), metadata propagation (`x-request-id` survives a hop), send/recv size-limit symmetry, and unary idempotency (exactly one side effect on retry). Nine buggy implementations each break one rule via injected components, surfaced as per-class violation counters with a `meets_contract()` predicate. 23 self-test scenarios.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `enum_accessor_oracle`, `SCENARIOS`.
- Planted-bad case: `enum_treated_as_open`, `deadline_not_decremented`, `stream_ignores_halfclose`, `quota_as_permission_denied`, `metadata_dropped_across_hop`, `idempotency_key_ignored`
- Oracle / proof target: Current proof target: `enum_accessor_oracle`, `SCENARIOS`.
- External testing pattern: grpc / proto contract fixture and regression testing.
- Usage note: Use this as a contract fixture for protobuf-style service definitions, required fields, enum handling, and backward-compatible RPC behavior.
- Current outside reference: gRPC documentation describes protobuf-backed service contracts and RPC method definitions. <https://grpc.io/docs/what-is-grpc/core-concepts/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/grpc_contract_test_harness.py`; `python harnesses/core/grpc_contract_test_harness.py --self-test`; `python harnesses/core/grpc_contract_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_grpc_contract_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/iot_telemetry`, `core/browser_e2e`.

## 66. Browser / E2E Surrogate Test Harness

- Name: Browser / E2E Surrogate Test Harness
- Path: `harnesses/core/browser_e2e_test_harness.py`
- Category: `core`
- Failure class: A deterministic DOM/E2E surrogate (no real browser, no asyncio): the DOM is immutable data, re-render is a pure tree mutation (`apply_mutation`), and async work is a manually-drained FIFO `EventLoop`. The oracle re-resolves selectors against the current DOM before clicking (no stale handle), asserts only after the loop settles, enforces event order (focus < input, change < click), raises `UnmockedRequestError` on unmocked requests, detects hydration structural mismatches (server vs client preorder), and prefers role/testid selectors over brittle absolute XPath. Six buggy implementations reproduce one flake each: stale-handle clicker, eager asserter, reordered event emitter, silent-404 fetch, hydration-blind renderer, brittle-XPath selector. 22 self-test scenarios. Complements `core/a11y` (accessibility tree) without overlapping it.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_auditor`, `SCENARIOS`.
- Planted-bad case: `broad_selector_auditor`, `green_flow_auditor`
- Oracle / proof target: Current proof target: `oracle_auditor`, `SCENARIOS`.
- External testing pattern: browser / e2e surrogate fixture and regression testing.
- Usage note: Use this as a browser-flow surrogate fixture for navigation, DOM state, user-visible assertions, and deterministic end-to-end behavior without a full external browser stack.
- Current outside reference: Playwright documentation frames browser end-to-end testing around user-visible page behavior. <https://playwright.dev/docs/intro>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/browser_e2e_test_harness.py`; `python harnesses/core/browser_e2e_test_harness.py --self-test`; `python harnesses/core/browser_e2e_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_browser_e2e_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/iot_telemetry`, `core/grpc_contract`.

## 67. Model / Embedding Drift Detection Test Harness

- Name: Model / Embedding Drift Detection Test Harness
- Path: `harnesses/ai/drift_detection_test_harness.py`
- Category: `ai`
- Failure class: Computes drift metrics by hand over fixed float fixtures (no numpy): PSI with an epsilon floor, KL and Jensen-Shannon divergence, Hellinger distance, embedding-centroid Euclidean displacement, query-document cosine-similarity drop, Spearman rank correlation of top-k neighbors, neighborhood churn, and query/index model-version mismatch. The oracle trips every alert on a planted-drift case and stays silent on a stable case. Seven buggy detectors each miss real drift or false-alarm on stable data: PSI with no epsilon floor (drops empty-bin terms), KL with swapped arguments, an averaged (washed-out) centroid distance, an unnormalized cosine, set overlap in place of a rank correlation, version-blind, and a stable-data false alarmer. Thresholds: PSI > 0.25, KL/JS > 0.20, Hellinger > 0.30, centroid > 0.50, cosine drop > 0.10, Spearman < 0.70, churn > 0.20. 24 self-test scenarios. Distinct from `ai/rag_eval` (retrieval/citation quality) and `ai/llm_eval` (answer graders).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_drift_detector`, `DRIFT_VERDICT_CORPUS`, `SCENARIOS`.
- Planted-bad case: `never_fires_threshold`, `fires_on_identical`, `psi_no_epsilon_floor`
- Oracle / proof target: Current proof target: `oracle_drift_detector`, `DRIFT_VERDICT_CORPUS`, `SCENARIOS`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as a statistical fixture for detecting distribution or embedding drift against a frozen baseline before treating model behavior as stable.
- Current outside reference: NIST AI RMF describes monitoring and managing AI risks across deployed systems. <https://www.nist.gov/itl/ai-risk-management-framework>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/drift_detection_test_harness.py`; `python harnesses/ai/drift_detection_test_harness.py --self-test`; `python harnesses/ai/drift_detection_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_drift_detection_test_harness`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/iot_telemetry`, `core/grpc_contract`, `core/browser_e2e`, `security/cwe_kev_regression`.

## 68. CWE / KEV Regression Test Harness

- Name: CWE / KEV Regression Test Harness
- Path: `harnesses/security/cwe_kev_regression_test_harness.py`
- Category: `security`
- Failure class: Maps high-frequency vulnerability classes to deterministic CWE/KEV-style fixtures. The general regression cases cover safe and unsafe inputs for XSS, SQL injection, CSRF, authorization bypass/IDOR, path traversal, command injection, code execution, dangerous upload extension, insecure deserialization, SSRF, and resource-limit abuse. The current TEETH oracle audits a frozen SQLi/path-traversal/XSS corpus with true-positive and false-positive cases, and the planted auditors model over-narrow SQLi and traversal detection plus over-broad XSS flagging.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_audit`, `AUDIT_CORPUS`, `CASES`.
- Planted-bad case: `weak_sqli_auditor`, `weak_traversal_auditor`, `overbroad_xss_auditor`
- Oracle / proof target: Current proof target: `oracle_audit`, `AUDIT_CORPUS`, `CASES`.
- External testing pattern: security regression and control-fixture testing.
- Usage note: Use this as a security-regression fixture for known weakness and exploited-vulnerability patterns represented by synthetic cases.
- Current outside reference: CISA KEV catalog documents known exploited vulnerabilities as a public security signal. <https://www.cisa.gov/known-exploited-vulnerabilities-catalog>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/security/cwe_kev_regression_test_harness.py`; `python harnesses/security/cwe_kev_regression_test_harness.py --self-test`; `python harnesses/security/cwe_kev_regression_test_harness.py --list-scenarios`; `python -m unittest tests.security.test_cwe_kev_regression_test_harness tests.security.test_cwe_kev_regression_proof`; `make test-security`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/iot_telemetry`, `core/grpc_contract`, `core/browser_e2e`, `ai/drift_detection`.

## Batch 14 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-14-iot-grpc-browser-drift-cwe-kev.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
