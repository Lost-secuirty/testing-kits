# Harness Map Batch 11

This file maps inventory entries #49-#53 in order: `core/dormant_code`, `core/cardinality`, `core/hermeticity`, `pharmacy/drug_interaction`, `ai/prompt_injection`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this campaign update is cut: `core/dormant_code` = `required`, `core/cardinality` = `required`, `core/hermeticity` = `required`, `pharmacy/drug_interaction` = `legacy`, `ai/prompt_injection` = `required`.

## 49. Dormant Code Test Harness

- Name: Dormant Code Test Harness
- Path: `harnesses/core/dormant_code_test_harness.py`
- Category: `core`
- Failure class: Untriggered branches that crash on first hit; surfaces dormant-path crashes via synthetic inputs.
- Logic shape: AND: reachable-line accounting, synthetic coverage extension, first-hit crash capture, paired tests, proof test, and TEETH swap-check must all hold. NOT: a planted dormant-path analyzer defect must not pass as if it were the oracle.
- Good case: `oracle_dormant_audit` matches the frozen dormant-line corpus and the synthetic self-test still surfaces the planted first-hit crash.
- Planted-bad case: `baseline_only_dormant_auditor`, `crash_blind_dormant_auditor`, `overcovered_dormant_auditor`.
- Oracle / proof target: Current proof target: `DORMANT_AUDIT_CORPUS`, `oracle_dormant_audit`, and `TEETH`.
- External testing pattern: dormant code fixture and regression testing.
- Usage note: Use this as a review fixture for code paths that appear reachable but are not exercised, especially before deleting or trusting old branches.
- Current outside reference: Coverage.py documents branch coverage as a way to identify code paths not exercised by tests. <https://coverage.readthedocs.io/en/latest/branch.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/dormant_code_test_harness.py`; `python harnesses/core/dormant_code_test_harness.py --self-test`; `python harnesses/core/dormant_code_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_dormant_code_test_harness tests.core.test_dormant_code_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/cardinality`, `core/hermeticity`.

## 50. Cardinality Test Harness

- Name: Cardinality Test Harness
- Path: `harnesses/core/cardinality_test_harness.py`
- Category: `core`
- Failure class: Metric/label/cache-key cardinality explosion (bounded vs unbounded).
- Logic shape: AND: distinct counts, sample counts, bounded/unbounded verdicts, paired tests, proof test, and TEETH swap-check must all hold. NOT: high-cardinality streams must not be collapsed into bounded evidence.
- Good case: `oracle_cardinality_audit` matches bounded, unbounded, and mixed-dimension frozen streams.
- Planted-bad case: `first_value_only_cardinality_auditor`, `sample_count_cardinality_auditor`, `first_dimension_only_cardinality_auditor`.
- Oracle / proof target: Current proof target: `CARDINALITY_AUDIT_CORPUS`, `oracle_cardinality_audit`, and `TEETH`.
- External testing pattern: cardinality fixture and regression testing.
- Usage note: Use this as a metrics fixture for high-cardinality labels, aggregation behavior, and alert-cost regressions.
- Current outside reference: OpenTelemetry defines cardinality as unique attribute values and notes high cardinality can affect telemetry backend performance and storage requirements. <https://opentelemetry.io/docs/concepts/glossary/#cardinality>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/cardinality_test_harness.py`; `python harnesses/core/cardinality_test_harness.py --self-test`; `python harnesses/core/cardinality_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_cardinality_test_harness tests.core.test_cardinality_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/dormant_code`, `core/hermeticity`.

## 51. Hermeticity Test Harness

- Name: Hermeticity Test Harness
- Path: `harnesses/core/hermeticity_test_harness.py`
- Category: `core`
- Failure class: Hidden test dependencies on HOME/env/network/time (top flaky-test class).
- Logic shape: AND: observation variation across time, random, env, HOME, and order probes must match the frozen oracle; paired tests, proof test, and TEETH swap-check must all hold. NOT: a clean observation stream must not be noisy-failed.
- Good case: `oracle_hermeticity_audit` keeps clean observations clean and flags each frozen contaminating dependency set.
- Planted-bad case: `baseline_only_hermeticity_auditor`, `time_random_only_hermeticity_auditor`, `noisy_hermeticity_auditor`.
- Oracle / proof target: Current proof target: `HERMETICITY_AUDIT_CORPUS`, `oracle_hermeticity_audit`, and `TEETH`.
- External testing pattern: hermeticity fixture and regression testing.
- Usage note: Use this as a test-environment fixture for hidden filesystem, network, clock, locale, or environment dependencies.
- Current outside reference: Bazel test encyclopedia describes hermetic test expectations such as controlled inputs and environment isolation. <https://bazel.build/reference/test-encyclopedia>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/hermeticity_test_harness.py`; `python harnesses/core/hermeticity_test_harness.py --self-test`; `python harnesses/core/hermeticity_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_hermeticity_test_harness tests.core.test_hermeticity_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/dormant_code`, `core/cardinality`.

## 52. Drug Interaction Test Harness

- Name: Drug Interaction Test Harness
- Path: `harnesses/pharmacy/drug_interaction_test_harness.py`
- Category: `pharmacy`
- Failure class: Drug–drug interaction checks and contraindication overridability.
- Logic shape: AND: legacy fixture behavior, paired tests, and inventory language must stay aligned. NOT: pharmacy-domain fixture checks must not be presented as clinical validation.
- Good case: The current legacy fixture exercises the software behavior summarized by the inventory: Drug–drug interaction checks and contraindication overridability.
- Planted-bad case: legacy proof path; no new TEETH claim is made in this mapping batch.
- Oracle / proof target: Current proof target: legacy fixture and paired-test behavior under the repo's pharmacy legacy handling.
- External testing pattern: fixture-defined pharmacy workflow regression testing, not clinical validation.
- Usage note: Use this as a fixture-defined interaction regression check only; it does not validate clinical interaction knowledge or treatment decisions.
- Current outside reference: No drug-interaction clinical validation is claimed; this maps fixture-defined interaction behavior only.
- Proof status: `legacy` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/pharmacy/drug_interaction_test_harness.py`; `python harnesses/pharmacy/drug_interaction_test_harness.py --self-test`; `python harnesses/pharmacy/drug_interaction_test_harness.py --list-scenarios`; `python -m unittest tests.pharmacy.test_drug_interaction_test_harness`; `make test-pharmacy`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. It also does not make clinical, medication-safety, or production healthcare claims. Legacy status means the proof model is not the same as current required TEETH entries.
- Related harnesses: `core/dormant_code`, `core/cardinality`, `core/hermeticity`, `ai/prompt_injection`.

## 53. Prompt Injection Test Harness

- Name: Prompt Injection Test Harness
- Path: `harnesses/ai/prompt_injection_test_harness.py`
- Category: `ai`
- Failure class: OWASP LLM01 injection corpus + system-prompt-leak guard, scored by precision/recall floors.
- Logic shape: AND: attack category, benign pass-through, corpus scoring, paired tests, proof test, and TEETH swap-check must all hold. NOT: benign word-overlap prompts must not be accepted as proof of an overbroad blocker.
- Good case: `oracle_prompt_injection_audit` matches direct, jailbreak, indirect, system-prompt-leak, role-confusion, and benign overlap frozen cases.
- Planted-bad case: `direct_only_prompt_guard`, `overbroad_keyword_prompt_guard`, `leak_blind_prompt_guard`, `delimiter_blind_prompt_guard`.
- Oracle / proof target: Current proof target: `PROMPT_INJECTION_AUDIT_CORPUS`, `oracle_prompt_injection_audit`, and `TEETH`.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as an LLM safety regression fixture for instruction-conflict, data-exfiltration, delimiter, and role-play attack patterns without calling a live model.
- Current outside reference: OWASP LLM01:2025 frames prompt injection as user or external content that changes model behavior in unintended ways; OWASP also tracks system prompt leakage separately. <https://genai.owasp.org/llmrisk/llm01-prompt-injection/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/prompt_injection_test_harness.py`; `python harnesses/ai/prompt_injection_test_harness.py --self-test`; `python harnesses/ai/prompt_injection_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_prompt_injection_test_harness tests.ai.test_prompt_injection_proof`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/dormant_code`, `core/cardinality`, `core/hermeticity`, `pharmacy/drug_interaction`.

## Batch 11 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-11-dormant-cardinality-hermeticity-drug-prompt.md`
- `docs/harness-map/README.md`

Scope note: this campaign update changes only the harnesses, paired proof tests, generated cards/ratchet, and current-state docs for these entries. It does not change workflows, hooks, dependencies, dashboard code, or central-map consolidation.
