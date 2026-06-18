# Harness Map

This document maps harnesses to proof shape, current proof status, and the wider testing pattern they demonstrate. It is descriptive, not an instruction source. Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

The map is current-state documentation. Entries are expected to change as the repo grows, as pending harnesses ratchet into TEETH `required`, and as stronger evidence replaces older self-test-only evidence. Do not treat this file as a permanent status pin or a substitute for `make proof`, CI, or direct source inspection.

## Status language

- `required` means the harness currently declares `TEETH` and is pinned as required by the current ratchet state.
- `pending` means the harness exists and is counted, but is not yet ratcheted into required TEETH proof.
- `legacy` means pharmacy-domain legacy proof handling.
- `subject to change` means the mapping is intentionally allowed to move when source, tests, TEETH status, or repo scope changes.

## Logic-shape labels

- `AND`: all named checks must pass.
- `NOT`: a forbidden condition must not appear.
- `NAND`: two dangerous conditions must never both be true.
- `XOR`: exactly one path, route, or state should be valid.
- `XNOR`: implementation output and independent/frozen oracle expectation must agree.

## Batch 1 — core foundation harnesses

Batch 1 covers the first five core harnesses in inventory order. These entries are intentionally compact and source-linked; they do not duplicate implementation code.

### 1. Stress Test Harness

- Name: Stress Test Harness
- Path: `harnesses/core/stress_harness.py`
- Category: `core`
- Failure class: load-shape distortion, hidden tail latency, throughput/error-rate visibility gaps, virtual-user exhaustion under stress.
- Logic shape: `AND`: scheduled requests, completed requests, latency metrics, status counts, and error counts must all be observable. `NOT`: closed-model pacing must not hide tail latency through coordinated omission.
- Good case: `--self-test` starts the stdlib mock server and drives a short stress run against it.
- Planted-bad case: none in TEETH yet. Current evidence is self-test/paired-test coverage, not planted-mutant proof.
- Oracle / proof target: corrected latency from scheduled send time, throughput/error accounting, scenario scheduling, and final report behavior.
- External testing pattern: open workload load testing / stress testing.
- Current outside reference: Grafana k6 documents open versus closed models and notes that closed models can reduce arrival rate when the system slows, a coordinated-omission risk; open arrival-rate executors decouple arrivals from iteration duration. <https://grafana.com/docs/k6/latest/using-k6/scenarios/concepts/open-vs-closed/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change.
- Commands: `python harnesses/core/stress_harness.py --self-test`; `python -m unittest tests.core.test_stress_harness`; `make test-core`; `make proof` for current global proof state.
- Known limits: does not prove production capacity, SLO compliance, distributed load realism, kernel/network bottlenecks, or correctness of a real target service. Current pending status means no TEETH mutant proof should be claimed.
- Related harnesses: `core/performance`-adjacent behavior lives here; compare with `core/chaos`, `core/memory`, `core/ratelimit`, and `core/network` when mapping resilience or resource pressure.

### 2. API / REST Test Harness

- Name: API / REST Test Harness
- Path: `harnesses/core/api_test_harness.py`
- Category: `core`
- Failure class: HTTP contract mismatch, wrong status code, missing response header, invalid response schema, missing required-field rejection.
- Logic shape: `AND`: status, content type, schema, and required headers must all match the frozen expectation. `NOT`: missing required input must not be accepted. `XNOR`: handler behavior must agree with the frozen request/response cases.
- Good case: create returns `201` with `Location`; seeded reads return `200`; missing IDs return `404`; invalid creates return `422`.
- Planted-bad case: `status_200_on_create`, `missing_location_header`, and `accepts_missing_required`.
- Oracle / proof target: `ORACLE_CASES` plus `_audit_response`; `prove()` judges each implementation response against frozen expected status/header/schema values, not against a live oracle result.
- External testing pattern: provider/API contract testing by concrete request/response examples.
- Current outside reference: Pact frames contract testing as checking whether HTTP/message integrations conform to a shared contract without relying on expensive brittle full integration tests. <https://docs.pact.io/>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/api_test_harness.py --self-test`; `python -m unittest tests.core.test_api_test_harness tests.core.test_api_proof`; `make test-core`; `make proof`.
- Known limits: does not prove every endpoint, full OpenAPI conformance, auth behavior, performance, data persistence, or consumer coverage. It proves the fixture-defined contract cases only.
- Related harnesses: `core/contract`, `core/graphql`, `core/grpc_contract`, `core/webhook`, `security/security`, `security/authz`.

### 3. Database Test Harness

- Name: Database Test Harness
- Path: `harnesses/core/db_test_harness.py`
- Category: `core`
- Failure class: SQL injection sink, partial transaction commit, non-durable/uncommitted write, final-state divergence.
- Logic shape: `AND`: values must be bound, error paths must roll back, and successful writes must commit visibly. `NAND`: untrusted value plus string-built SQL must never both be present. `XNOR`: observed final DB state must match frozen literal expectations.
- Good case: safe data-access operations bind values, roll back the whole unit of work on error, and commit writes so a fresh reader can see them.
- Planted-bad case: `string_interpolated_sql_injection`, `no_rollback_on_error`, and `forgot_to_commit`.
- Oracle / proof target: `DB_CORPUS`; `prove()` compares observed final-state dictionaries to literal expected values using fresh in-memory SQLite fixtures.
- External testing pattern: database transaction/injection regression testing.
- Current outside reference: Python's `sqlite3` docs explicitly recommend placeholders instead of string formatting to avoid SQL injection and note that insert transactions must be committed before changes are saved. <https://docs.python.org/3/library/sqlite3.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/db_test_harness.py --self-test`; `python -m unittest tests.core.test_db_test_harness tests.core.test_db_proof`; `make test-core`; `make proof`.
- Known limits: SQLite fixtures do not prove behavior for PostgreSQL/MySQL isolation levels, distributed transactions, real migration systems, ORM behavior, or production durability under crash/restart.
- Related harnesses: `core/serialization`, `core/schema_evolution`, `core/concurrency`, `security/security`, `security/appsec`.

### 4. Web Scraper Test Harness

- Name: Web Scraper Test Harness
- Path: `harnesses/core/scraper_test_harness.py`
- Category: `core`
- Failure class: extraction drift, robots.txt non-compliance, unbounded pagination cycle, scraper determinism failure.
- Logic shape: `AND`: extraction, robots policy handling, and pagination termination must all match the frozen corpus. `NOT`: disallowed robots paths must not be treated as allowed. `NAND`: next-page traversal plus no visited-set must never produce an unbounded crawl. `XNOR`: scraper result must match the frozen expected output.
- Good case: table extraction preserves the expected field/header structure, robots rules are honored, and pagination terminates through a bounded crawl.
- Planted-bad case: `drops_header_row`, `ignores_robots_disallow`, and `unbounded_next_page_cycle`.
- Oracle / proof target: `TEETH_CORPUS`; `prove()` compares scraper output directly to frozen expected values without network, filesystem, RNG, or clock access.
- External testing pattern: deterministic scraper fixture testing for extraction, crawl policy, and pagination guards.
- Current outside reference: Scrapy documents `ROBOTSTXT_OBEY` as the setting that makes Scrapy respect robots.txt policies; this maps to the harness's robots-policy fixture, though the harness remains stdlib-only. <https://docs.scrapy.org/en/latest/topics/settings.html#robotstxt-obey>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/scraper_test_harness.py --self-test`; `python -m unittest tests.core.test_scraper_test_harness tests.core.test_scraper_proof`; `make test-core`; `make proof`.
- Known limits: does not prove real web compliance, JavaScript rendering, site-specific terms, large crawl scheduling, anti-bot behavior, or legal permission to scrape. Robots.txt is a policy signal, not an enforcement guarantee.
- Related harnesses: `core/fuzz`, `core/pagination`, `core/i18n`, `core/search_relevance`, `security/appsec`.

### 5. CLI Tool Test Harness

- Name: CLI Tool Test Harness
- Path: `harnesses/core/cli_test_harness.py`
- Category: `core`
- Failure class: wrong exit code, invalid argument acceptance, mutually exclusive flag acceptance, subcommand misrouting, missing operand acceptance.
- Logic shape: `AND`: exit code, action, and output stream must all match the frozen CLI expectation. `NOT`: invalid args, conflicting flags, and too-few operands must not succeed. `XOR`: each subcommand must dispatch to exactly its intended handler. `XNOR`: observed CLI outcome must equal the frozen expected `CliOutcome`.
- Good case: `--help`, `--version`, valid `add`, valid `list`, and valid `list --format json` return expected success outcomes.
- Planted-bad case: `usage_error_exits_zero`, `accepts_mutually_exclusive`, `misroutes_subcommand`, and `skips_required_operands`.
- Oracle / proof target: `CLI_CORPUS`; `prove()` compares parser/dispatch output to frozen literal `CliOutcome` values with no subprocess, clock, network, filesystem, or RNG.
- External testing pattern: command-line interface contract testing / black-box CLI behavior checks.
- Current outside reference: Python's `argparse` docs describe argument parsing, generated help/usage, and automatic errors for invalid user arguments; this maps to the harness's exit/action/stream expectations. <https://docs.python.org/3/library/argparse.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/cli_test_harness.py --self-test`; `python -m unittest tests.core.test_cli_test_harness tests.core.test_cli_proof`; `make test-core`; `make proof`.
- Known limits: does not prove shell quoting, OS-specific terminal behavior, packaging entry points, real subprocess I/O, localization, or every possible parser edge. It proves the fixture-defined parser/dispatch contract.
- Related harnesses: `core/config`, `core/contract`, `core/serialization`, `core/statemachine`, `core/api`.

## Batch 1 closeout

Docs checked in this batch:

- `README.md`
- `HARNESS_ROADMAP.md`
- `docs/DOCS_MAP.md`
- `docs/HARNESS_READING_GUIDE.md`
- `docs/REVIEWER_QUICKSTART.md`
- `docs/HARNESS_MAP.md`
- `docs/LEARNINGS.md`
- `llms.txt`

## Batch 2 — security and resilience foundation harnesses

Batch 2 covers harnesses #6-#10 in inventory order. It also carries forward the Batch 1 review lesson by keeping batch metadata out of individual dossiers.

### 6. Security Test Harness

- Name: Security Test Harness
- Path: `harnesses/security/security_test_harness.py`
- Category: `security`
- Failure class: injection sink exposure, reflected XSS, command injection, path traversal, header/CRLF injection, authentication bypass, sensitive-data exposure.
- Logic shape: `AND`: scanners, mock endpoints, result status, severity, evidence, and remediation fields must stay observable. `NOT`: known unsafe payloads must not be reflected or accepted by safe endpoints. `XNOR`: scanner results should match the fixture-defined safe/vulnerable endpoint split.
- Good case: safe endpoint variants sanitize, reject, or omit dangerous input; protected endpoints require the valid token; profile-safe omits password/API-key fields.
- Planted-bad case: none in TEETH yet. Current evidence is self-test/paired-test coverage over safe/vulnerable fixtures, not planted-mutant proof.
- Oracle / proof target: scanner verdicts over the built-in mock server fixtures for SQL injection, XSS, command injection, path traversal, CRLF/header injection, auth, and sensitive-data exposure.
- External testing pattern: web application security testing / attack-payload regression testing.
- Current outside reference: OWASP WSTG lists web-application security testing areas including authentication, authorization, input validation, SQL injection, command injection, HTTP response splitting, host-header injection, and API testing. <https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change.
- Commands: `python harnesses/security/security_test_harness.py --self-test`; `python -m unittest tests.security.test_security_test_harness`; `make test-security`; `make proof` for current global proof state.
- Known limits: does not prove production security, scanner completeness, exploitability, authz correctness, CSRF coverage, browser execution, or absence of vulnerabilities. Pending status means no TEETH mutant proof should be claimed.
- Related harnesses: `security/appsec`, `security/authz`, `security/jwt`, `security/pii_redaction`, `security/cwe_kev_regression`, `core/api`, `core/fuzz`.

### 7. Chaos / Resilience Test Harness

- Name: Chaos / Resilience Test Harness
- Path: `harnesses/core/chaos_test_harness.py`
- Category: `core`
- Failure class: circuit-breaker threshold error, missing OPEN fast-reject, faulty recovery edge, resilience-state-machine drift.
- Logic shape: `AND`: the breaker must trip on exact threshold, reject while open, recover through half-open, and re-open on failed probe. `NOT`: OPEN state must not serve calls before cooldown. `XNOR`: observed `(state, was_rejected)` timeline must match frozen literal expectations.
- Good case: `oracle_run` reproduces every `BREAKER_CORPUS` timeline and the self-test asserts TEETH.
- Planted-bad case: `trips_one_late` and `serves_while_open`.
- Oracle / proof target: `BREAKER_CORPUS`; `prove()` compares deterministic circuit-breaker timelines against hand-computed literal observations using an injected integer step-clock, not real time or sockets.
- External testing pattern: chaos/resilience testing with controlled fault experiments and steady-state disruption checks.
- Current outside reference: Principles of Chaos Engineering defines chaos engineering as controlled experimentation to build confidence under turbulent production conditions and describes steady-state hypotheses plus real-world event variables. <https://principlesofchaos.org/>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/chaos_test_harness.py --self-test`; `python harnesses/core/chaos_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_chaos_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove distributed-system resilience, production blast-radius safety, real dependency behavior, traffic realism, or SLO durability. TEETH proves only the fixture-defined breaker transition corpus and planted breaker defects.
- Related harnesses: `core/stress`, `core/network`, `core/ratelimit`, `core/circuitbreaker`, `core/tracing`, `core/queue`.

### 8. Memory / Soak Test Harness

- Name: Memory / Soak Test Harness
- Path: `harnesses/core/memory_test_harness.py`
- Category: `core`
- Failure class: leak false positive, leak false negative, threshold-boundary error, span-based memory-spike misclassification, resource-counter drift.
- Logic shape: `AND`: RSS series analysis, threshold handling, object lifecycle accounting, and TEETH swap-check must all hold. `NOT`: noisy-but-flat memory should not be reported as a leak. `XNOR`: leak verdicts must match frozen literal expectations.
- Good case: `oracle_analyze` reproduces every `LEAK_CORPUS` verdict; object tracker reports an unbalanced created/destroyed count as a leak.
- Planted-bad case: `threshold_boundary` and `peak_minus_min`.
- Oracle / proof target: `LEAK_CORPUS`; `prove()` compares deterministic leak verdicts for frozen RSS integer series against hand-derived literals using the shared `TEETH_THRESHOLD`.
- External testing pattern: memory-allocation tracing and leak-regression testing.
- Current outside reference: Python `tracemalloc` is documented as a debug tool for tracing memory blocks, allocation tracebacks, per-line allocation statistics, and snapshot differences to detect memory leaks. <https://docs.python.org/3/library/tracemalloc.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/memory_test_harness.py --self-test`; `python harnesses/core/memory_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_memory_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove production memory stability, allocator behavior across platforms, long soak duration realism, C-extension leaks, or OS-level RSS accuracy. TEETH proves only the frozen leak-regression corpus and lifecycle invariant.
- Related harnesses: `core/stress`, `core/chaos`, `core/concurrency`, `core/error_path_leak`, `core/hermeticity`.

### 9. Concurrency Test Harness

- Name: Concurrency Test Harness
- Path: `harnesses/core/concurrency_test_harness.py`
- Category: `core`
- Failure class: lost update, missing lock, broken critical section, non-atomic check-then-act, overdraft-style invariant violation.
- Logic shape: `AND`: lock coverage, read-modify-write atomicity, guarded update, and deterministic schedule replay must all hold. `NAND`: shared mutable state plus missing/broken lock must never produce an accepted final state. `XNOR`: simulated final state must equal frozen literal expectations.
- Good case: `oracle_impl` preserves increments and refuses overdraft under the forced interleavings.
- Planted-bad case: `missing_lock_lost_update`, `lock_dropped_before_write`, and `nonatomic_check_then_act_overdraft`.
- Oracle / proof target: `SCENARIOS`; `prove()` drives single-thread deterministic step programs under frozen adversarial schedules and compares final shared-cell state to hand-computed literals.
- External testing pattern: concurrency-control and synchronization testing with explicit lock/condition semantics.
- Current outside reference: Python `threading` documents lock acquisition/release behavior, the need to pair acquires/releases, deadlock risk, and condition-variable wait/notify behavior under an associated lock. <https://docs.python.org/3/library/threading.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/concurrency_test_harness.py --self-test`; `python harnesses/core/concurrency_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_concurrency_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove real scheduler coverage, all data races, CPU memory-model behavior, async concurrency, multiprocessing, or deadlock freedom in production. TEETH intentionally avoids real thread timing and proves only frozen interleavings.
- Related harnesses: `core/memory`, `core/chaos`, `core/db`, `core/queue`, `core/pipeline`, `core/hermeticity`.

### 10. Fuzz Test Harness

- Name: Fuzz Test Harness
- Path: `harnesses/core/fuzz_test_harness.py`
- Category: `core`
- Failure class: crash on malformed input, fixed-width integer overflow, unescaped delimiter handling, empty/None off-by-one/null-dereference defect.
- Logic shape: `AND`: frozen input replay, crash recording, crash deduplication, and TEETH swap-check must all hold. `NOT`: robust target must not crash on any frozen adversarial input. `XNOR`: crash/no-crash verdict must match the frozen corpus expectation.
- Good case: `oracle_target` survives every `_FUZZ_CORPUS` input and deterministic replay produces the same crash count.
- Planted-bad case: `int32_overflow`, `unescaped_delimiter`, and `empty_off_by_one`.
- Oracle / proof target: `_FUZZ_CORPUS`; `prove()` replays a fixed list through `FuzzRunner.fuzz_with_inputs` and treats any recorded crash as the harness flagging the implementation.
- External testing pattern: fuzz testing / malformed-input crash discovery.
- Current outside reference: OWASP describes fuzzing as automatically providing unexpected, malformed, or semi-malformed inputs to identify bugs, vulnerabilities, or unexpected behavior. <https://owasp.org/www-community/Fuzzing>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/fuzz_test_harness.py --self-test`; `python harnesses/core/fuzz_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_fuzz_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove exhaustive input safety, coverage-guided depth, parser grammar coverage, sanitizer coverage, or absence of crashes beyond the frozen corpus and configured generators. TEETH proves only deterministic replay over pinned adversarial inputs.
- Related harnesses: `core/property`, `core/mutation`, `core/scraper`, `security/security`, `security/appsec`, `security/upload`.

## Batch 2 closeout

Docs checked in this batch:

- `HARNESS_ROADMAP.md`
- `docs/HARNESS_READING_GUIDE.md`
- `docs/HARNESS_MAP.md`
- `docs/LEARNINGS.md`

## Batch 3 — property, mutation, snapshot, contract, and serialization harnesses

Batch 3 covers harnesses #11-#15 in inventory order. It keeps proof status tied to the current ratchet state rather than to historical inventory wording.

### 11. Property-Based Test Harness

- Name: Property-Based Test Harness
- Path: `harnesses/core/property_test_harness.py`
- Category: `core`
- Failure class: non-minimal counterexample, shrinker disabled by early return, shrinker overshoot to a passing input, invariant/counterexample reporting drift.
- Logic shape: `AND`: generated property checks, shrinking behavior, frozen shrink corpus, and TEETH swap-check must all hold. `NOT`: a reported counterexample must not be a passing input. `XNOR`: shrink output must match the frozen minimal literal.
- Good case: `oracle_shrink` shrinks frozen integer/list failures to the smallest still-failing literal values in `SHRINK_CORPUS`.
- Planted-bad case: `stops_early` and `overshoots`.
- Oracle / proof target: `SHRINK_CORPUS`; `prove()` compares shrinker output against hand-computed minimal counterexamples and excludes RNG-driven property generation from the TEETH path.
- External testing pattern: property-based testing with shrinking/minimal counterexample reporting.
- Current outside reference: Hypothesis documents property-based testing for Python, including generating examples and simplifying failing examples to make bugs easier to understand. <https://hypothesis.readthedocs.io/en/latest/>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/property_test_harness.py --self-test`; `python harnesses/core/property_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_property_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove exhaustive input coverage, Hypothesis compatibility, statistical generator quality, or correctness of every property predicate. TEETH proves only deterministic shrinking over the frozen corpus.
- Related harnesses: `core/fuzz`, `core/mutation`, `core/statistical_rng_oracle`, `core/numeric`, `core/serialization`.

### 12. Mutation Test Harness

- Name: Mutation Test Harness
- Path: `harnesses/core/mutation_test_harness.py`
- Category: `core`
- Failure class: survived mutant, weak assertion suite, mutation operator blind spot, timeout/error classification drift.
- Logic shape: `AND`: mutant generation, test execution, result classification, score calculation, and reporting must all remain observable. `NOT`: a killed mutant must not be counted as survived. `XNOR`: generated mutation reports should match the fixture-defined result classification.
- Good case: `--self-test` mutates a small `classify` function and confirms multiple mutants are generated, at least one mutant is killed, and the mutation score is within `(0, 1]`.
- Planted-bad case: none in TEETH yet. Current evidence is self-test/paired-test coverage, not planted-mutant TEETH proof.
- Oracle / proof target: mutation operators over source strings plus `MutationRunner` result classification: `KILLED`, `SURVIVED`, `ERROR`, and `TIMEOUT`.
- External testing pattern: mutation testing for test-suite strength.
- Current outside reference: mutmut documents Python mutation testing as changing code and checking whether tests fail, exposing test-suite gaps when mutants survive. <https://mutmut.readthedocs.io/en/latest/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change.
- Commands: `python harnesses/core/mutation_test_harness.py --self-test`; `python -m unittest tests.core.test_mutation_test_harness`; `make test-core`; `make proof` for current global proof state.
- Known limits: pending status means no TEETH mutant proof should be claimed. Regex mutation operators do not prove semantic mutation completeness, equivalent-mutant handling, real subprocess isolation, or production-grade sandbox security.
- Related harnesses: `core/property`, `core/fuzz`, `core/complexity`, `core/ci_workflow_hardening`, `security/supplychain`.

### 13. Regression & Snapshot Test Harness

- Name: Regression & Snapshot Test Harness
- Path: `harnesses/core/regression_snapshot_test_harness.py`
- Category: `core`
- Failure class: shallow snapshot comparison, order-sensitive false regression, missed nested value regression, noisy snapshot drift.
- Logic shape: `AND`: normalized comparison, nested traversal, checksum-backed snapshots, and TEETH swap-check must all hold. `NOT`: reordered dict keys must not create a false regression. `XNOR`: comparator verdict must match frozen `COMPARE_CORPUS` literals.
- Good case: `oracle_match` treats dict key order as irrelevant at every depth while still reporting changed nested values as mismatches.
- Planted-bad case: `no_recurse` and `order_sensitive`.
- Oracle / proof target: `COMPARE_CORPUS`; `prove()` compares pure `(actual, stored) -> bool` verdicts against frozen expected match booleans and excludes clock/filesystem-backed snapshot persistence from TEETH.
- External testing pattern: snapshot/regression testing with normalized structural comparison.
- Current outside reference: pytest-regressions documents regression helpers for checking data, files, and numeric outputs against previously saved expected values. <https://pytest-regressions.readthedocs.io/en/latest/>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/regression_snapshot_test_harness.py --self-test`; `python harnesses/core/regression_snapshot_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_regression_snapshot_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove snapshot approval policy, golden-file review quality, filesystem durability, timestamp stability, or broad UI snapshot correctness. TEETH proves only normalized comparison against the frozen structural corpus.
- Related harnesses: `core/serialization`, `core/config`, `core/schema_evolution`, `core/search_relevance`, `core/contract`.

### 14. Contract / Interface Test Harness

- Name: Contract / Interface Test Harness
- Path: `harnesses/core/contract_test_harness.py`
- Category: `core`
- Failure class: precondition/postcondition miss, interface drift, invariant failure, backward-incompatible schema/contract change accepted as compatible.
- Logic shape: `AND`: preconditions, postconditions, type checks, invariants, compatibility corpus, and TEETH swap-check must all hold. `NOT`: removed fields, changed types, and new required fields must not be accepted as compatible. `XNOR`: compatibility verdict must match frozen `COMPAT_CASES` literals.
- Good case: `check_compatibility` accepts compatible changes such as optional-field additions and enum widening, and rejects removed fields, type changes, new required fields, enum narrowing, and optional-to-required changes.
- Planted-bad case: `ignores_removed_field`, `ignores_type_change`, and `allows_new_required_field`.
- Oracle / proof target: `COMPAT_CASES`; `prove()` compares compatibility verdicts to frozen literal expectations and the self-test also routes the oracle through the harness's `Contract` and `InvariantChecker` machinery.
- External testing pattern: design-by-contract / consumer-facing compatibility contract testing.
- Current outside reference: icontract documents Python design-by-contract support for preconditions, postconditions, invariants, and runtime contract checking. <https://icontract.readthedocs.io/en/latest/>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/contract_test_harness.py --self-test`; `python harnesses/core/contract_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_contract_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove all API compatibility rules, distributed consumer coverage, semantic versioning policy, OpenAPI/Pact coverage, or production runtime behavior. TEETH proves only the frozen backward-compatibility corpus and planted checker defects.
- Related harnesses: `core/api`, `core/config`, `core/serialization`, `core/graphql`, `core/grpc_contract`, `core/webhook`.

### 15. Serialization / Roundtrip Test Harness

- Name: Serialization / Roundtrip Test Harness
- Path: `harnesses/core/serialization_test_harness.py`
- Category: `core`
- Failure class: lossy roundtrip, dropped field, int-to-float precision corruption, tuple-to-list coercion missed, fixed-schema field loss.
- Logic shape: `AND`: encode/decode, loss detection, field preservation, frozen corpus, and TEETH swap-check must all hold. `NOT`: serializers must not silently drop falsy fields or schema-unknown fields. `XNOR`: decoded value and lossy-field verdict must match frozen `SER_CORPUS` literals.
- Good case: `oracle_roundtrip` preserves clean JSON fields, flags tuple-to-list loss, keeps `2**53 + 1` exact in JSON, and flags binary schema drops.
- Planted-bad case: `drops_falsy_field`, `int_float_coercion_blind`, and `schema_drop_blind`.
- Oracle / proof target: `SER_CORPUS`; `prove()` compares decoded field values and lossy-field verdicts against frozen literal expectations using pure in-process JSON/binary roundtrips.
- External testing pattern: serialization/deserialization roundtrip testing and data-loss detection.
- Current outside reference: Python's `pickle` docs define serialization/deserialization for Python objects and the standard library also provides data-format modules such as `json`, `csv`, and `configparser` used by this harness. <https://docs.python.org/3/library/pickle.html>
- Proof status: `required`; subject to change if the TEETH ratchet or source changes.
- Commands: `python harnesses/core/serialization_test_harness.py --self-test`; `python harnesses/core/serialization_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_serialization_test_harness`; `make test-core`; `make proof`.
- Known limits: does not prove arbitrary format compatibility, untrusted pickle safety, schema migration correctness, cross-language interoperability, or all floating-point/data-model edge cases. TEETH proves only the frozen JSON/binary loss-detection corpus.
- Related harnesses: `core/db`, `core/config`, `core/regression_snapshot`, `core/schema_evolution`, `core/api`.

## Batch 3 closeout

Docs checked in this batch:

- `HARNESS_INVENTORY.md`
- `HARNESS_ROADMAP.md`
- `docs/HARNESS_MAP.md`
- `cards/teeth_ratchet.json`
