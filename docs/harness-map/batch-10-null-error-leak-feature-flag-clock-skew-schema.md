# Harness Map Batch 10

This file maps inventory entries #44-#48 in order: `core/null_propagation`, `core/error_path_leak`, `core/feature_flag`, `core/clock_skew`, `core/schema_evolution`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/null_propagation` = `required`, `core/error_path_leak` = `required`, `core/feature_flag` = `required`, `core/clock_skew` = `pending`, `core/schema_evolution` = `required`.

## 44. Null Propagation Test Harness

- Name: Null Propagation Test Harness
- Path: `harnesses/core/null_propagation_test_harness.py`
- Category: `core`
- Failure class: None propagating through a call chain (the #1 AI-coded bug class); flags never-guarded None paths.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `_COMPOSITE_ORACLE`, `PROBE_CORPUS`.
- Planted-bad case: `deep_deref_crash`, `silent_none_coercion`, `nan_propagation`, `missing_key_crash`, `empty_index_crash`
- Oracle / proof target: Current proof target: `_COMPOSITE_ORACLE`, `PROBE_CORPUS`.
- External testing pattern: null propagation fixture and regression testing.
- Usage note: Use this as a guardrail fixture for None/null handling through transformations, defaults, and joins where silent propagation could hide defects.
- Current outside reference: Python `None` is the language null object; this harness maps fixture-defined propagation and guard behavior. <https://docs.python.org/3/library/constants.html#None>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/null_propagation_test_harness.py`; `python harnesses/core/null_propagation_test_harness.py --self-test`; `python harnesses/core/null_propagation_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_null_propagation_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/error_path_leak`, `core/feature_flag`, `core/clock_skew`, `core/schema_evolution`.

## 45. Error Path Leak Test Harness

- Name: Error Path Leak Test Harness
- Path: `harnesses/core/error_path_leak_test_harness.py`
- Category: `core`
- Failure class: Resource acquire/release leaks on error/exception paths; double-release and unbalanced-cleanup detection.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_handle`, `LEAK_CORPUS`.
- Planted-bad case: `echo_raw_exception`, `leak_internal_path`, `leak_secret_debug_field`
- Oracle / proof target: Current proof target: `oracle_handle`, `LEAK_CORPUS`.
- External testing pattern: error path leak fixture and regression testing.
- Usage note: Use this as a negative-path regression check for cleanup, redaction, and resource release when exceptions or early returns occur.
- Current outside reference: Python context managers and `with` statements document structured cleanup behavior around exceptional paths. <https://docs.python.org/3/reference/compound_stmts.html#the-with-statement>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/error_path_leak_test_harness.py`; `python harnesses/core/error_path_leak_test_harness.py --self-test`; `python harnesses/core/error_path_leak_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_error_path_leak_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/null_propagation`, `core/feature_flag`, `core/clock_skew`, `core/schema_evolution`.

## 46. Feature Flag Test Harness

- Name: Feature Flag Test Harness
- Path: `harnesses/core/feature_flag_test_harness.py`
- Category: `core`
- Failure class: Flag flips mid-call, rollout/kill-switch consistency (the Google June-2025 outage class).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `good_pricer`.
- Planted-bad case: `expectation_violation`, `dormant_path_crash`, `return_type_drift`
- Oracle / proof target: Current proof target: `good_pricer`.
- External testing pattern: feature flag fixture and regression testing.
- Usage note: Use this as a fixture for flag evaluation precedence, defaulting, targeting, and stale-flag behavior before wiring flags into release paths.
- Current outside reference: OpenFeature documents feature flag evaluation concepts and provider-neutral flagging APIs. <https://openfeature.dev/specification/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/feature_flag_test_harness.py`; `python harnesses/core/feature_flag_test_harness.py --self-test`; `python harnesses/core/feature_flag_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_feature_flag_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/null_propagation`, `core/error_path_leak`, `core/clock_skew`, `core/schema_evolution`.

## 47. Clock Skew Test Harness

- Name: Clock Skew Test Harness
- Path: `harnesses/core/clock_skew_test_harness.py`
- Category: `core`
- Failure class: Distributed-time bugs: NTP jumps, monotonic regression, cross-node skew vs TTL/LWW merges.
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: clock skew fixture and regression testing.
- Usage note: Use this as a timing fixture for monotonic-vs-wall-clock behavior, expiry windows, and clock-jump regressions.
- Current outside reference: Python `time` documents monotonic clocks for elapsed-time measurement independent of wall-clock adjustments. <https://docs.python.org/3/library/time.html#time.monotonic>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/clock_skew_test_harness.py`; `python harnesses/core/clock_skew_test_harness.py --self-test`; `python harnesses/core/clock_skew_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_clock_skew_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/null_propagation`, `core/error_path_leak`, `core/feature_flag`, `core/schema_evolution`.

## 48. Schema Evolution Test Harness

- Name: Schema Evolution Test Harness
- Path: `harnesses/core/schema_evolution_test_harness.py`
- Category: `core`
- Failure class: Reader/writer schema drift; backward/forward compatibility (silent pipeline schema drift).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `is_breaking`, `MIGRATION_CORPUS`, `SCENARIOS`.
- Planted-bad case: `drop_blind`, `narrow_blind`, `required_additive`
- Oracle / proof target: Current proof target: `is_breaking`, `MIGRATION_CORPUS`, `SCENARIOS`.
- External testing pattern: schema evolution fixture and regression testing.
- Usage note: Use this as a compatibility fixture for reader/writer schema changes, default values, unknown fields, and backwards/forwards migration assumptions.
- Current outside reference: Avro schema resolution documents reader/writer schema compatibility concepts. <https://avro.apache.org/docs/current/specification/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/schema_evolution_test_harness.py`; `python harnesses/core/schema_evolution_test_harness.py --self-test`; `python harnesses/core/schema_evolution_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_schema_evolution_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/null_propagation`, `core/error_path_leak`, `core/feature_flag`, `core/clock_skew`.

## Batch 10 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-10-null-error-leak-feature-flag-clock-skew-schema.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
