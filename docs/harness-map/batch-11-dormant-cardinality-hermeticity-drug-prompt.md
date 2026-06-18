# Harness Map Batch 11

This file maps inventory entries #49-#53 in order: `core/dormant_code`, `core/cardinality`, `core/hermeticity`, `pharmacy/drug_interaction`, `ai/prompt_injection`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/dormant_code` = `pending`, `core/cardinality` = `pending`, `core/hermeticity` = `pending`, `pharmacy/drug_interaction` = `legacy`, `ai/prompt_injection` = `pending`.

## 49. Dormant Code Test Harness

- Name: Dormant Code Test Harness
- Path: `harnesses/core/dormant_code_test_harness.py`
- Category: `core`
- Failure class: Untriggered branches that crash on first hit; surfaces dormant-path crashes via synthetic inputs.
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: dormant code fixture and regression testing.
- Usage note: Use this as a review fixture for code paths that appear reachable but are not exercised, especially before deleting or trusting old branches.
- Current outside reference: Coverage.py documents branch coverage as a way to identify code paths not exercised by tests. <https://coverage.readthedocs.io/en/latest/branch.html>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/dormant_code_test_harness.py`; `python harnesses/core/dormant_code_test_harness.py --self-test`; `python harnesses/core/dormant_code_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_dormant_code_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/cardinality`, `core/hermeticity`.

## 50. Cardinality Test Harness

- Name: Cardinality Test Harness
- Path: `harnesses/core/cardinality_test_harness.py`
- Category: `core`
- Failure class: Metric/label/cache-key cardinality explosion (bounded vs unbounded).
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: cardinality fixture and regression testing.
- Usage note: Use this as a metrics fixture for high-cardinality labels, aggregation behavior, and alert-cost regressions.
- Current outside reference: OpenTelemetry metrics guidance warns that high-cardinality attributes can cause cost and performance problems. <https://opentelemetry.io/docs/specs/otel/metrics/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/cardinality_test_harness.py`; `python harnesses/core/cardinality_test_harness.py --self-test`; `python harnesses/core/cardinality_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_cardinality_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/dormant_code`, `core/hermeticity`.

## 51. Hermeticity Test Harness

- Name: Hermeticity Test Harness
- Path: `harnesses/core/hermeticity_test_harness.py`
- Category: `core`
- Failure class: Hidden test dependencies on HOME/env/network/time (top flaky-test class).
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: hermeticity fixture and regression testing.
- Usage note: Use this as a test-environment fixture for hidden filesystem, network, clock, locale, or environment dependencies.
- Current outside reference: Bazel test encyclopedia describes hermetic test expectations such as controlled inputs and environment isolation. <https://bazel.build/reference/test-encyclopedia>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/hermeticity_test_harness.py`; `python harnesses/core/hermeticity_test_harness.py --self-test`; `python harnesses/core/hermeticity_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_hermeticity_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
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
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as an LLM safety regression fixture for instruction-conflict, data-exfiltration, delimiter, and role-play attack patterns without calling a live model.
- Current outside reference: OWASP LLM Top 10 covers prompt injection as LLM01 and frames it as an instruction-conflict/security risk. <https://owasp.org/www-project-top-10-for-large-language-model-applications/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/prompt_injection_test_harness.py`; `python harnesses/ai/prompt_injection_test_harness.py --self-test`; `python harnesses/ai/prompt_injection_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_prompt_injection_test_harness`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/dormant_code`, `core/cardinality`, `core/hermeticity`, `pharmacy/drug_interaction`.

## Batch 11 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-11-dormant-cardinality-hermeticity-drug-prompt.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
