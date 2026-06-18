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
