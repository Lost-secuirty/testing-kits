# Harness Map Batch 4

This file maps Batch 4 harnesses in inventory order while avoiding replacement of `docs/HARNESS_MAP.md` through a truncated connector view. It is descriptive, not command authority. Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Batch 4 covers harnesses #16-#20:

- `core/config`
- `core/logging`
- `core/network`
- `core/pipeline`
- `core/datetime`

Proof status is tied to the current `cards/teeth_ratchet.json` state. `core/network` is mapped as `pending`; the other four are mapped as `required`.

## 16. Configuration Validation Test Harness

- Name: Configuration Validation Test Harness
- Path: `harnesses/core/config_test_harness.py`
- Category: `core`
- Failure class: configuration precedence regression, missing required setting accepted, type coercion skipped, deploy-time override ignored, misconfigured service booting instead of failing fast.
- Logic shape: `AND`: defaults, file config, environment overrides, required-field validation, type coercion, frozen corpus, and TEETH swap-check must all hold. `NOT`: a missing required field must not be accepted. `XNOR`: loaded effective config and error keys must match frozen `ORACLE_CASES` literals.
- Good case: `load_config` applies `defaults < file < env`, coerces env strings such as `"6543"` to integers, rejects uncoercible values, and reports missing required fields.
- Planted-bad case: `env_not_overriding_file`, `missing_required_accepted`, and `no_type_coercion`.
- Oracle / proof target: `ORACLE_CASES`; `prove()` compares `LoadOutcome` values against frozen expected literals without clock, network, filesystem I/O, or RNG.
- External testing pattern: configuration validation and layered-configuration precedence testing.
- Current outside reference: Python `configparser` documents configuration files, defaults, multi-file read precedence where later files override earlier ones, and getter methods for integer/float/boolean conversion. <https://docs.python.org/3/library/configparser.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/config_test_harness.py --self-test`; `python harnesses/core/config_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_config_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove every real application config format, secret-manager integration, cloud-provider environment behavior, live deployment safety, or all cross-field validation rules. TEETH proves only the frozen layered-config corpus and planted loader defects.
- Related harnesses: `core/contract`, `core/serialization`, `core/regression_snapshot`, `core/schema_evolution`, `security/pii_redaction`, `security/supplychain`.

## 17. Logging / Observability Test Harness

- Name: Logging / Observability Test Harness
- Path: `harnesses/core/logging_test_harness.py`
- Category: `core`
- Failure class: invalid structured log accepted, missing required field accepted, spoofed/unknown log level accepted, malformed timestamp accepted, observability-field drift.
- Logic shape: `AND`: required fields, timestamp validation, level allowlist, frozen log corpus, and TEETH swap-check must all hold. `NOT`: incomplete or spoofed log entries must not validate. `XNOR`: structured-log validity verdicts must match frozen `LOG_CORPUS` literals.
- Good case: `oracle_validate` accepts well-formed entries with `timestamp`, `level`, and `message`, rejects missing `message`/`timestamp`, rejects levels such as `TRACE` and `haxx`, and rejects malformed or impossible timestamps.
- Planted-bad case: `skips_level_allowlist` and `skips_required_fields`.
- Oracle / proof target: `LOG_CORPUS`; `prove()` compares validator verdicts against hand-decided literal expectations over frozen dictionaries.
- External testing pattern: structured logging contract testing / observability event-shape validation.
- Current outside reference: Python `logging` documents standard log levels and the logging facility's event-recording model; this maps to the harness's level allowlist and structured event checks. <https://docs.python.org/3/library/logging.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/logging_test_harness.py --self-test`; `python harnesses/core/logging_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_logging_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove production log routing, indexing, dashboard correctness, real distributed trace propagation, log retention, or absence of all sensitive data leaks. TEETH proves only the frozen structured-log validity corpus and planted validator defects.
- Related harnesses: `core/tracing`, `core/config`, `core/regression_snapshot`, `security/pii_redaction`, `security/appsec`.

## 18. Network / Protocol Test Harness

- Name: Network / Protocol Test Harness
- Path: `harnesses/core/network_test_harness.py`
- Category: `core`
- Failure class: protocol-format mismatch, timeout handling gap, retry/backoff drift, payload-size handling failure, connection-pool exhaustion/reuse bug, graceful-shutdown race, DNS failure crash.
- Logic shape: `AND`: protocol checks, timeout checks, retry checks, payload checks, pool checks, shutdown checks, and DNS failure handling must all be observable. `NOT`: invalid hostnames and network errors must not crash the harness. `XNOR`: report fields should match the fixture-defined network scenario outcomes.
- Good case: `run_all()` records protocol, timeout, retry, payload, pool, shutdown, and DNS checks in a `NetworkReport`; invalid hosts return failed `ConnectionResult` values instead of uncaught exceptions.
- Planted-bad case: none in TEETH yet. Current evidence is self-test/paired-test style network behavior coverage, not planted-mutant TEETH proof.
- Oracle / proof target: `NetworkReport` over the built-in mock server and network helper classes: `ProtocolTester`, `TimeoutTester`, `RetryTester`, `PayloadTester`, `ConnectionPoolTester`, `ShutdownTester`, and `DNSTester`.
- External testing pattern: protocol/network resilience fixture testing.
- Current outside reference: Python `socket` documents low-level networking and timeout behavior; this maps to the harness's connection timeout, listener readiness, DNS failure, and connection lifecycle checks. <https://docs.python.org/3/library/socket.html>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change.
- Commands: `python harnesses/core/network_test_harness.py`; `python -m unittest tests.core.test_network_test_harness`; `make test-core`; `make proof` for current global proof state.
- Known limits: does not prove production network reliability, kernel/socket behavior across platforms, packet loss, TLS correctness, remote DNS behavior, proxy behavior, or distributed timeout policy. Pending status means no TEETH mutant proof should be claimed.
- Related harnesses: `core/chaos`, `core/stress`, `core/ratelimit`, `core/circuitbreaker`, `core/webhook`, `core/grpc_contract`.

## 19. Data Pipeline / ETL Test Harness

- Name: Data Pipeline / ETL Test Harness
- Path: `harnesses/core/pipeline_test_harness.py`
- Category: `core`
- Failure class: group aggregation drift, average denominator bug, first-value drop in reducer, schema/null/dedup/reconciliation visibility gap, ETL transform regression.
- Logic shape: `AND`: group key projection, count, sum, average, normalized ordering, frozen aggregate corpus, and TEETH swap-check must all hold. `NOT`: a group reducer must not silently drop the first value. `XNOR`: per-group `COUNT`, `SUM`, and `AVG` must match frozen `AGG_CORPUS` literals.
- Good case: `oracle_aggregate` computes `eng`, `hr`, and `ops` aggregates with hand-computed counts, totals, and averages.
- Planted-bad case: `avg_div_by_groupcount` and `sum_skips_first`.
- Oracle / proof target: `AGG_CORPUS`; `prove()` compares normalized `GroupAgg` tuples against frozen literal aggregates and excludes mock HTTP server behavior from TEETH.
- External testing pattern: ETL/data-pipeline validation and data-quality testing.
- Current outside reference: recent ELT pipeline testing work frames pipeline validation around orchestration checks, declarative tests, anomaly injection, and cross-store consistency checks for data quality. <https://arxiv.org/abs/2605.20500>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/pipeline_test_harness.py --self-test`; `python harnesses/core/pipeline_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_pipeline_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove production ETL correctness, real data quality, orchestration behavior, cross-store consistency, schema drift detection outside the fixture, or performance at pipeline scale. TEETH proves only the frozen group-aggregation corpus and planted reducer defects.
- Related harnesses: `core/db`, `core/schema_evolution`, `core/regression_snapshot`, `core/serialization`, `core/concurrency`, `core/search_relevance`.

## 20. Time / DateTime Test Harness

- Name: Time / DateTime Test Harness
- Path: `harnesses/core/datetime_test_harness.py`
- Category: `core`
- Failure class: leap-year rule regression, naive every-four-years rule, forgotten century `%400` exception, timezone awareness misuse, DST gap/fold edge drift.
- Logic shape: `AND`: Gregorian leap-year literals, century exceptions, oracle predicate, planted mutants, and TEETH swap-check must all hold. `NOT`: a naive `year % 4 == 0` rule must not pass. `XNOR`: leap-year verdicts must match frozen `LEAP_CORPUS` literals.
- Good case: `oracle_is_leap` matches ordinary leap/common years and discriminating century cases: 1600, 1900, 2000, and 2100.
- Planted-bad case: `every_4th` and `forgets_400`.
- Oracle / proof target: `LEAP_CORPUS`; `prove()` compares `is_leap(year)` verdicts against hand-written Gregorian literals with no clock, network, server, threads, filesystem, or RNG.
- External testing pattern: calendrical edge-case regression testing / datetime correctness testing.
- Current outside reference: Python `calendar.isleap()` exposes leap-year calculation, and Python `datetime` documents aware/naive datetime objects and timezone handling. <https://docs.python.org/3/library/calendar.html> <https://docs.python.org/3/library/datetime.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/datetime_test_harness.py --self-test`; `python harnesses/core/datetime_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_datetime_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove all calendrical math, timezone database behavior, locale-specific calendar rules, scheduling semantics, NTP/clock-skew behavior, or production date storage. TEETH proves only the Gregorian leap-year frozen corpus and planted leap-rule defects.
- Related harnesses: `core/clock_skew`, `core/lexical_date_canonicalization`, `core/config`, `core/statemachine`, `core/regression_snapshot`.

## Batch 4 closeout

Docs checked in this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- `docs/HARNESS_MAP_BATCH_4.md`

Central map note: `docs/HARNESS_MAP.md` was not replaced in this pass because the connector returned truncated content for that large file. This standalone batch file preserves the mapping without risking accidental deletion of earlier batches. A later safe consolidation pass can fold this section into `docs/HARNESS_MAP.md` using a non-truncated local checkout or verified full-file content.
