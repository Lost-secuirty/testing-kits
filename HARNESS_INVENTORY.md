# Test Harness Inventory

**Total: 77 harnesses.** Per-harness self-test and proof status is generated locally by
`make report` and uploaded by CI as a workflow artifact. Entries #1–43 are
documented in full below; #44–53 are bridged in a compact table; #54–59,
#60–62, #63–67, #68–72, #73, and #74–77 follow in full. As of the `fix-status-green` work every
harness passes `--self-test`; the formerly-failing `srs`/`hermeticity` bugs
and the Windows portability gaps are resolved.

`STATUS.md` is generated output, not the canonical source of truth. The source
of truth is the harness code, paired unittest suites, proof audit output, and
current CI/test output.

## How to interpret this inventory

Inventory count is catalog scope, not proof strength. Use this file to find
harnesses and paired tests; use proof/status sources to decide whether a harness
is currently ratcheted and passing.

For proof interpretation, prefer:

- `docs/GOLDEN_STATS.md` for the latest human-maintained snapshot index;
- `cards/teeth_ratchet.json` when present for required/pending/legacy class;
- generated `STATUS.md` / `STATUS.json` artifacts for a fresh report run;
- `tools/proof_audit.py` output for the actual proof result;
- harness cards and proof tests for contract, mutants, and known limits.

Do not treat a catalog entry as proof that the target behavior is production-safe.

> Harnesses 20–24 were added after a 2020–2026 gap audit of failure modes most
> commonly missed by AI-assisted / “vibe” coding: time/timezone correctness,
> idempotency & retry-safety, state-machine validity, numeric/money precision,
> and authorization (broken access control). Same pure-stdlib pattern as the
> rest.
>
> Harnesses 25–30 were added after a 2026 web-researched gap audit targeting the
> next wave of commonly-missed surfaces: LLM/AI-feature evaluation (the defining
> 2026 test surface), error-path / negative coverage (the #1 AI-code failure
> mode — happy-path-only code), caching correctness, rate-limiting / throttling,
> webhook delivery & signature verification, and i18n / Unicode / encoding.
> Pagination/cursor-consistency and accessibility were identified but deferred to
> a later batch. Batch-size rule: up to **10 harnesses per batch when UPGRADING
> existing harnesses** (the 2026 teeth campaign), **6 for brand-new harnesses**.
>
> Harnesses 31–36 (batch 2) were added after a 12-agent read-only audit of the
> whole collection: pagination/cursor consistency, accessibility (static a11y),
> agentic-AI / tool-calling eval, supply-chain / build reproducibility, file-upload
> / decompression-bomb / streaming ingest, and an app-security harness (SSRF /
> insecure-deserialization / JWT / XXE / open-redirect / mass-assignment) that
> complements the injection-focused Security harness #6. The audit also produced a
> per-harness within-harness gap list and a project-hygiene backlog — see
> `HARNESS_ROADMAP.md`.
>
> Harnesses 37–43 are pharmacy-domain-specific, added after auditing
> `lostsoulfs/pharmacy-app`. Each fills a gap the 36 generic harnesses could not
> cover: SM-2 spaced-repetition algorithm correctness and DB persistence,
> clinical calculator safety oracles (patient-harm-critical domain), temporal
> PIN lockout (time-windowed attempt counting), SQLite online backup/restore
> lifecycle, rotating-capped audit log ring-buffer semantics, date-window expiry
> alerting (calendar arithmetic + SQLite under a controllable clock), and
> partial-fill two-phase ledger (open→resolve exactly-once contract).

All harnesses are pure Python stdlib — zero external dependencies. Almost all include a mock HTTP server (the Database, CLI, SRS, Backup-Restore, and Expiry-Window harnesses are the exceptions — SQLite- and subprocess-driven respectively, with no networked server), a CLI with `--self-test` mode, and a matching root-level `test_*.py` unittest suite. The Dice Duel reliability lab is isolated under `dice_duel_lab/` and is not part of generic harness discovery.

Batch 7 adds a proof-test convention: new harnesses keep the paired unittest file and add `test_<name>_proof.py` when a planted bad fixture is needed to prove the harness catches the failure mode.

---

## 1. Stress Test Harness

**File:** `harnesses/core/stress_harness.py` (744 lines)
**Tests:** `tests/core/test_stress_harness.py` — 52 tests
**Port:** 8080 (default)

Hammers an HTTP endpoint with concurrent requests to find performance limits. Measures throughput (req/sec), latency percentiles (p50/p95/p99), error rates, and connection failures under load. Supports configurable concurrency, duration, ramp-up, and request patterns. Uses asyncio for high-throughput load generation with live reporting.

**Key components:** StressConfig, StressReport, LatencyHistogram, StressRunner, MockStressHandler

---

## 2. API / REST Test Harness

**File:** `harnesses/core/api_test_harness.py` (803 lines)
**Tests:** `tests/core/test_api_test_harness.py` — 64 tests
**Port:** 18900

Tests REST API correctness — request/response validation, status codes, content types, header checks, JSON schema verification, CRUD lifecycle, pagination, error handling, and rate limiting. Validates that endpoints conform to their contracts.

**Key components:** ApiTestCase, ApiTestSuite, RequestBuilder, ResponseValidator, SchemaChecker, MockApiHandler

---

## 3. Database Test Harness

**File:** `harnesses/core/db_test_harness.py` (579 lines)
**Tests:** `tests/core/test_db_test_harness.py` — 37 tests
**Port:** none (SQLite backend; no networked mock server)

Tests database operations — CRUD correctness, transaction isolation, constraint enforcement, migration safety, connection pool behavior, query performance, and data integrity under concurrent access. Uses an in-memory SQLite mock.

**Key components:** DbTestRunner, MigrationChecker, TransactionTester, ConnectionPoolMonitor, MockDbHandler

---

## 4. Web Scraper Test Harness

**File:** `harnesses/core/scraper_test_harness.py` (646 lines)
**Tests:** `tests/core/test_scraper_test_harness.py` — 46 tests
**Port:** 18910

Tests web scraping reliability — HTML parsing, CSS/XPath selectors, pagination following, redirect handling, rate limiting compliance, robots.txt respect, error recovery, and content extraction accuracy.

**Key components:** ScraperTestRunner, SelectorValidator, PaginationTester, RateLimitChecker, MockScraperHandler

---

## 5. CLI Tool Test Harness

**File:** `harnesses/core/cli_test_harness.py` (535 lines)
**Tests:** `tests/core/test_cli_test_harness.py` — 51 tests
**Port:** none (subprocess-driven; no networked mock server)

Tests command-line tool behavior — argument parsing, exit codes, stdout/stderr output, flag combinations, help text, error messages, piped input, and signal handling. Runs CLI commands via subprocess and validates outputs.

**Key components:** CliTestRunner, OutputValidator, ExitCodeChecker, ArgParser, MockCliHandler

---

## 6. Security Test Harness

**File:** `harnesses/security/security_test_harness.py` (756 lines)
**Tests:** `tests/security/test_security_test_harness.py` — 38 tests
**Port:** 18920

Scans for security vulnerabilities — SQL injection, XSS, command injection, path traversal, header injection, CSRF, authentication bypass, and sensitive data exposure. Runs attack payloads against endpoints and checks for reflected/executed content.

**Key components:** InjectionScanner, XSSScan, CommandInjectionScan, PathTraversalScan, HeaderSecurityAudit, MockSecurityHandler

---

## 7. Chaos / Resilience Test Harness

**File:** `harnesses/core/chaos_test_harness.py` (575 lines)
**Tests:** `tests/core/test_chaos_test_harness.py` — 46 tests
**Port:** 18930

Tests system resilience under failure conditions — circuit breaker state machine (CLOSED/OPEN/HALF_OPEN), fault injection (latency, errors, timeouts, corruption), retry logic, graceful degradation, and recovery behavior.

**Key components:** CircuitBreaker, FaultInjector, ResilienceTestRunner, ResilienceMetrics, MockChaosHandler

---

## 8. Memory / Soak Test Harness

**File:** `harnesses/core/memory_test_harness.py` (611 lines)
**Tests:** `tests/core/test_memory_test_harness.py` — 38 tests
**Port:** 18940

Detects memory leaks and resource exhaustion — RSS monitoring over time, GC object count tracking, linear regression on memory growth, file descriptor usage, thread count monitoring, object lifecycle tracking (create/destroy balance), and GC pressure measurement.

**Key components:** MemorySnapshot, LeakReport, ObjectTracker, SoakTestRunner, GCPressureReport, MockMemoryHandler

---

## 9. Concurrency Test Harness

**File:** `harnesses/core/concurrency_test_harness.py` (657 lines)
**Tests:** `tests/core/test_concurrency_test_harness.py` — 31 tests
**Port:** 18950

Tests thread safety and synchronization — race condition detection (locked vs unlocked counters), deadlock detection with lock ordering violations, atomicity checking (read-modify-write), concurrent collection safety (list/dict), producer-consumer queue correctness, barrier synchronization, and countdown latches.

**Key components:** SharedCounter, RaceDetector, DeadlockDetector, AtomicityChecker, ProducerConsumerTest, BarrierTest, MockConcurrencyHandler

---

## 10. Fuzz Test Harness

**File:** `harnesses/core/fuzz_test_harness.py` (764 lines)
**Tests:** `tests/core/test_fuzz_test_harness.py` — 48 tests
**Port:** 18960

Feeds random, malformed, and adversarial inputs into functions to find crashes and unhandled edge cases. Includes boundary value exploration (systematic edge-case probing), crash classification by exception type, and deduplication via fingerprinting. Generators cover ints, floats, strings, bytes, lists, dicts with special values (NaN, inf, null bytes, SQL injection strings, etc.).

**Key components:** FuzzRunner, BoundaryExplorer, CrashClassifier, CrashRecord, FuzzReport, fuzz_int/float/string/bytes/list/dict generators, MockFuzzHandler

---

## 11. Property-Based Test Harness

**File:** `harnesses/core/property_test_harness.py` (871 lines)
**Tests:** `tests/core/test_property_test_harness.py` — 57 tests
**Port:** 18970

Defines invariants (properties) that must hold across thousands of random inputs, then automatically shrinks failing inputs to find the minimal counterexample. Supports preconditions to filter invalid inputs. Generators are composable (gen_int, gen_float, gen_string, gen_list, gen_tuple, gen_dict, gen_one_of).

**Key components:** Property, PropertyRunner, PropertySuite, Shrinker (int/float/string/list/dict/tuple shrinking), PropertyReport, MockPropertyHandler

---

## 12. Mutation Test Harness

**File:** `harnesses/core/mutation_test_harness.py` (846 lines)
**Tests:** `tests/core/test_mutation_test_harness.py` — 47 tests
**Port:** 18980

Injects small code changes (mutants) into Python source code and checks if a test suite catches them. If tests still pass with a mutant, it “survived” — revealing a gap in test coverage. Six mutation operators work on source strings via regex: arithmetic swap, comparison swap, constant swap, boolean swap, return swap, and condition negation.

**Key components:** Mutator (6 operators), MutationRunner, MutationReport, SourceMutator, MutationResult (KILLED/SURVIVED/ERROR/TIMEOUT), MockMutationHandler

---

## 13. Regression & Snapshot Test Harness

**File:** `harnesses/core/regression_snapshot_test_harness.py` (830 lines)
**Tests:** `tests/core/test_regression_snapshot_test_harness.py` — 40 tests
**Port:** 18990

Captures known-good outputs as baselines, then re-runs and compares to detect regressions. Snapshot store persists baselines as JSON files with SHA256 checksums. Comparator supports unified diff, JSON-normalized comparison (key-order insensitive), and line-level comparison with optional whitespace ignoring.

**Key components:** Snapshot, SnapshotStore, SnapshotComparator, RegressionRunner, RegressionTest, SuiteReport, MockRegressionHandler

---

## 14. Contract / Interface Test Harness

**File:** `harnesses/core/contract_test_harness.py`
**Tests:** `tests/core/test_contract_test_harness.py` — 72 tests
**Port:** 19000

Validates function contracts (preconditions, postconditions, type specs, return types), interface compliance (required methods/attributes with signature checking), and invariants that must hold across sequences of operations.

**Key components:** ViolationType, ContractViolation, Contract, ContractChecker, InterfaceSpec, InterfaceChecker, InvariantChecker, MockContractHandler

---

## 15. Serialization / Roundtrip Test Harness

**File:** `harnesses/core/serialization_test_harness.py`
**Tests:** `tests/core/test_serialization_test_harness.py` — 80 tests
**Port:** 19010

Tests encode→decode roundtrip survival across six formats: JSON, CSV, binary/struct, pickle, XML, and INI/ConfigParser. Detects lossy conversions (tuple→list, precision loss, type coercion) and validates edge cases like Unicode, NaN, inf, and empty collections.

**Key components:** SerializationFormat, RoundtripResult, FormatTester, LossDetector, RoundtripRunner, SerializationReport, MockSerializationHandler

---

## 16. Configuration Validation Test Harness

**File:** `harnesses/core/config_test_harness.py`
**Tests:** `tests/core/test_config_test_harness.py` — 52 tests
**Port:** 19020

Validates configuration against schemas — required keys, types, value ranges, enum/regex constraints, cross-field dependencies, environment variable overrides, type coercion, nested dotted paths, and sensitive value detection (plaintext passwords, tokens, API keys).

**Key components:** FieldSchema, ConfigSchema, ConfigValidator, EnvOverrideChecker, CrossFieldValidator, SensitiveValueDetector, ConfigReport, MockConfigHandler

---

## 17. Logging / Observability Test Harness

**File:** `harnesses/core/logging_test_harness.py`
**Tests:** `tests/core/test_logging_test_harness.py` — 88 tests
**Port:** 19030

Validates structured logging — JSON format compliance, log level hierarchy/filtering, sensitive data leak scanning (passwords, SSNs, credit cards, API keys), correlation ID propagation, ISO 8601 timestamps, performance timing fields, error context enrichment, output destination routing, and log sampling/rate limiting.

**Key components:** LogEntry, LogFormatValidator, LogLevelChecker, SensitiveDataScanner, CorrelationTracker, TimestampValidator, PerformanceLogChecker, ErrorContextChecker, LoggingReport, MockLoggingHandler

---

## 18. Network / Protocol Test Harness

**File:** `harnesses/core/network_test_harness.py`
**Tests:** `tests/core/test_network_test_harness.py` — 55 tests
**Port:** 19040

Tests network behavior — TCP connect/disconnect lifecycle, connection/read timeouts, retry with exponential backoff, keep-alive reuse, large payload handling, concurrent connection limits, connection pool (checkout/return/expiry), graceful shutdown, DNS resolution, and half-open connection detection.

**Key components:** ConnectionConfig, ConnectionResult, RetryPolicy, ProtocolTester, TimeoutTester, RetryTester, PayloadTester, ConnectionPoolTester, ShutdownTester, DNSTester, NetworkReport, MockNetworkHandler

---

## 19. Data Pipeline / ETL Test Harness

**File:** `harnesses/core/pipeline_test_harness.py`
**Tests:** `tests/core/test_pipeline_test_harness.py` — 78 tests
**Port:** 19050

Tests data pipeline correctness — schema conformance, null handling (drop/default/propagate), deduplication, row count reconciliation, aggregation (SUM/COUNT/AVG/MIN/MAX), joins (inner/left/right), filtering, sorting stability, type transformations, idempotency, and throughput measurement.

**Key components:** PipelineStage, SchemaSpec, SchemaValidator, NullHandler, Deduplicator, Reconciler, Aggregator, Joiner, PipelineRunner, PipelineReport, MockPipelineHandler

---

## 20. Time / DateTime Test Harness

**File:** `harnesses/core/datetime_test_harness.py`
**Tests:** `tests/core/test_datetime_test_harness.py` — 73 tests
**Port:** 19060

Targets the single most common AI-code time bug class. Uses an injectable `Clock` (freeze/tick) so time logic is testable without real waits. Covers timezone offset conversion and naive-vs-aware detection, DST spring-forward gaps and fall-back folds, leap-year validity (Feb 29 in 2024/2000 vs 2023/1900), boundary cases (epoch 0, pre-epoch negatives, the 2038 32-bit edge, far-future), ISO 8601 parse/format round-trips, and `timedelta`/monotonic-vs-wall duration arithmetic.

**Key components:** Clock, TimezoneTester, DSTTester, LeapYearTester, BoundaryTester, ParseFormatTester, DurationTester, ServerTimeTester, MockDateTimeHandler

---

## 21. Idempotency / Retry-Safety Test Harness

**File:** `harnesses/core/idempotency_test_harness.py`
**Tests:** `tests/core/test_idempotency_test_harness.py` — 66 tests
**Port:** 19070

Tests retry-safety: idempotency keys, an atomic check-and-set dedup store (PENDING/COMPLETED/FAILED + TTL + persisted response artifact), retry convergence (replay returns identical cached response, side-effect counter does not advance), concurrent duplicate suppression via `threading.Barrier` (exactly-once execution), and the “state-only store loses the response” failure mode. Classifies idempotent vs non-idempotent HTTP methods.

**Key components:** IdempotencyStore, StateOnlyStore, KeyDedupTester, RetryConvergenceTester, ConcurrentDedupTester, InProgressTester, TTLTester, ResponsePersistenceTester, SafeMethodTester, ServerIdempotencyTester, MockIdempotencyHandler

---

## 22. State Machine Test Harness

**File:** `harnesses/core/statemachine_test_harness.py`
**Tests:** `tests/core/test_statemachine_test_harness.py` — 82 tests
**Port:** 19080

Validates finite-state-machine correctness with a generic `StateMachine` (states, initial, `Transition` rules, terminal set) that raises `InvalidTransition` and leaves state unchanged on rejection. Drives an order-lifecycle example (CREATED→PAID→SHIPPED→DELIVERED, CANCELLED, terminal states) plus fixture machines (orphaned-state, cyclic, acyclic, non-deterministic) to exercise reachability/dead-state detection, cycle detection, transition-coverage tracking, and determinism checking.

**Key components:** StateMachine, Transition, InvalidTransition, TransitionTester, InvalidTransitionTester, ReachabilityAnalyzer, CycleDetector, CoverageTracker, DeterminismChecker, ServerStateMachineTester, MockStateMachineHandler

---

## 23. Numeric / Money Precision Test Harness

**File:** `harnesses/core/numeric_test_harness.py`
**Tests:** `tests/core/test_numeric_test_harness.py` — 102 tests
**Port:** 19090

Demonstrates and guards against silent numeric bugs. A `Money` helper on `decimal.Decimal` (configurable rounding, default banker’s ROUND_HALF_EVEN, largest-remainder allocation) is the correct reference against which naive float math is shown to fail: 0.1+0.2≠0.3, accumulation drift, wrong-rounding, big+small precision loss (1e16+1==1e16), float overflow to inf, NaN comparison oddities, and `Fraction` exactness. Bill-splitting allocations sum back to the exact total with no lost cent.

**Key components:** Money, FloatPitfallTester, RoundingModeTester, CurrencyTester, OverflowTester, PrecisionTester, ComparisonTester, ServerNumericTester, MockNumericHandler

---

## 24. Authorization / Access-Control Test Harness

**File:** `harnesses/security/authz_test_harness.py`
**Tests:** `tests/security/test_authz_test_harness.py` — 110 tests
**Port:** 19100

Focuses on authorization correctness (OWASP #1, distinct from the injection-focused Security harness). A `Role` enum (ANONYMOUS/USER/EDITOR/ADMIN) and `AccessControl` engine combine RBAC grants with per-resource ownership checks, deny-by-default, and revocation-overrides-grant. Asserts the full role×action matrix, vertical privilege escalation denials, horizontal/IDOR denials (user A cannot touch user B’s object), least-privilege defaulting on forged/missing role claims, and token-scope defense-in-depth. The mock server enforces identical rules over HTTP via a `Bearer id:role:scopes` token, returning 200/401/403/404.

**Key components:** Role, Permission, Resource, AccessControl, RBACTester, VerticalEscalationTester, HorizontalEscalationTester, PrivilegeBoundaryTester, TokenScopeTester, ServerAuthzTester, MockAuthzHandler

---

## 25. LLM / AI-Feature Eval Test Harness

**File:** `harnesses/ai/llm_eval_test_harness.py`
**Tests:** `tests/ai/test_llm_eval_test_harness.py` — 117 tests
**Port:** 19110

Tests LLM-backed features without calling any real model. A seeded `MockLLM` is deterministic at temperature 0 (reproducible) and perturbs output via HMAC at temperature > 0 (controlled non-determinism), and refuses dangerous prompts. Four graders score outputs by semantic equivalence rather than byte-exact match: ExactMatch, SemanticOverlap (token-set Jaccard), RegexFormat, and a deterministic JudgeStub (LLM-as-judge). A ConsistencyChecker asserts pass-rate over N samples under temperature; an InjectionTester runs a 15-item adversarial corpus (ignore-previous-instructions, system-prompt exfiltration, delimiter/role-play jailbreaks) against a rule-based guardrail; a RefusalChecker verifies safety refusals. An EvalSuite produces an aggregate EvalReport with per-case transcripts.

**Key components:** MockLLM, ExactMatchGrader, SemanticOverlapGrader, RegexFormatGrader, JudgeStubGrader, ConsistencyChecker, InjectionTester, RefusalChecker, EvalSuite, EvalReport, MockLLMEvalHandler

---

## 26. Error-Path / Negative Coverage Test Harness

**File:** `harnesses/core/errorpath_test_harness.py`
**Tests:** `tests/core/test_errorpath_test_harness.py` — 107 tests
**Port:** 19120

Targets the #1 AI-generated-code failure mode: happy-path-only code that skips error/null/exception/early-return branches. A `CoverageProbe` records which labelled branches execute across an input battery and flags never-hit error branches (a deliberately-broken `broken_divide` that omits the null guard is caught). An ExceptionPathTester forces each declared exception type and asserts the right type, message, and unchanged state after failure (no partial mutation). A NullHandlingTester injects None into every parameter position; a BoundaryTester fires guard clauses (empty/zero/negative/oversize); a TimeoutTester aborts a slow op cleanly with no partial data; a ResourceCleanupTester verifies try/finally release via acquire/release counters (with a leaking impl flagged).

**Key components:** CoverageProbe, ExceptionPathTester, NullHandlingTester, BoundaryTester, TimeoutTester, ResourceCleanupTester, BranchResult, NegativeCaseResult, ErrorPathReport, MockErrorPathHandler

---

## 27. Caching Correctness Test Harness

**File:** `harnesses/core/cache_test_harness.py`
**Tests:** `tests/core/test_cache_test_harness.py` — 118 tests
**Port:** 19130

Tests the classic silent-failure surface: caches that “work” in the demo but serve stale/wrong data under writes and concurrency. A `Cache` (injectable clock, per-entry TTL, max-size LRU) is the correct reference; a `BuggyCache` that skips invalidation proves the harness catches stale-after-write. Covers TTL expiry (just-before vs just-after via clock advance), invalidation-on-write, cache stampede / thundering herd (a `SingleFlightCache` computes a cold key exactly once via per-key lock while a `NaiveCache` races to N loader calls), negative caching with its own TTL, true LRU eviction (least-recently-*used*, recency updated on touch), and namespace key-collision isolation. CacheStats tracks hits/misses/evictions/ratio.

**Key components:** Cache, BuggyCache, SingleFlightCache, NaiveCache, CacheEntry, CacheStats, CacheTestResult, CacheReport, MockCacheHandler

---

## 28. Rate Limiting / Throttling Test Harness

**File:** `harnesses/core/ratelimit_test_harness.py`
**Tests:** `tests/core/test_ratelimit_test_harness.py` — 129 tests
**Port:** 19140

Tests rate-limiter correctness across four algorithms, all driven by an injectable `FakeClock` (no real sleeps). TokenBucket (burst to capacity, correct refill math, tokens never exceed cap), LeakyBucket (steady drain), FixedWindow (the classic boundary-burst bug — 2× limit across a window edge is detected and reported as the known weakness), and SlidingWindow (proven to prevent that burst). PerKeyTokenBuckets gives independent buckets per API key/IP. A 429 + Retry-After path yields the correct wait, and advancing the clock by it admits the next request. A threaded concurrency stress asserts a locked bucket never over-admits, with a naive unlocked counter illustrating the TOCTOU race.

**Key components:** FakeClock, TokenBucket, LeakyBucket, FixedWindow, SlidingWindow, PerKeyTokenBuckets, RateLimitDecision, LimiterStats, RateLimitReport, MockRateLimitHandler

---

## 29. Webhook Delivery / Verification Test Harness

**File:** `harnesses/core/webhook_test_harness.py`
**Tests:** `tests/core/test_webhook_test_harness.py` — 120 tests
**Port:** 19150

Tests webhook reliability — a surface that fails silently in production. HMAC-SHA256 signature verification accepts valid signatures and rejects tampered bodies, wrong secrets, and malformed headers, using constant-time `hmac.compare_digest` (a naive `==` compare is flagged as timing-unsafe). A timestamp tolerance / replay window (injectable clock) rejects stale and replayed-but-valid signatures. At-least-once delivery is deduped per event-id (exactly-once side effects), complementary to the Idempotency harness. A sender retries 5xx/timeout with exponential backoff (asserted schedule, max-attempts cap, 2xx stops retries), a flaky receiver is delivered eventually, exhausted events land in a dead-letter queue with reason, and sequence numbers detect out-of-order / gapped delivery.

**Key components:** WebhookEvent, DeliveryAttempt, DeliveryResult, WebhookReport, signature sign/verify, DedupTester, OrderingTester, retry/backoff sender, dead-letter queue, MockWebhookHandler

---

## 30. i18n / Unicode / Encoding Test Harness

**File:** `harnesses/core/i18n_test_harness.py`
**Tests:** `tests/core/test_i18n_test_harness.py` — 122 tests
**Port:** 19160

Tests text-handling correctness that AI code routinely botches (distinct from the format-focused Serialization harness #15). Covers encoding round-trips (utf-8/utf-16/latin-1/ascii) with mojibake detection (utf-8 bytes decoded as latin-1), BOM detection/stripping (utf-8-sig, utf-16 LE/BE), surrogate-pair / astral-plane handling (emoji code-point vs UTF-16-unit vs byte counts all differ; lone surrogates flagged), grapheme-vs-code-point counting (ZWJ family emoji: naive `len()`=7 vs grapheme=1), NFC/NFD normalization (byte-unequal but normalize-equal; the un-normalized dedup bug is demonstrated), casefolding (German ß → ss, Turkish dotless-ı trap), byte-safe truncation and East-Asian display width, and RTL/bidi detection including a flagged Trojan-Source bidi-override injection.

**Key components:** EncodingResult, NormalizationResult, GraphemeResult, I18nReport, encoding round-trip + mojibake detection, BOM detection, grapheme estimator, NFC/NFD normalization, casefold compare, safe_truncate_bytes, bidi/RTL detection, MockI18nHandler

---

## 31. Pagination / Cursor Consistency Test Harness

**File:** `harnesses/core/pagination_test_harness.py`
**Tests:** `tests/core/test_pagination_test_harness.py` — 127 tests
**Port:** 19170

Tests pagination correctness over a mutable dataset. A thread-safe `BackingStore` is paginated two ways: `OffsetPaginator` (LIMIT/OFFSET) and `CursorPaginator` (keyset on a `(sort_key, id)` tiebreaker with an opaque base64 cursor). Proves the two classic offset bugs — a row deleted before the offset makes the next page SKIP a row, and a row inserted before the offset makes it RE-SHOW a row — and shows the cursor paginator is immune to both. Also covers unstable ordering without a tiebreaker, last-page/boundary cases (empty last page, exact-multiple, limit > dataset, limit ≤ 0 rejected), full-traversal reconciliation (every row seen exactly once), and cursor tamper-rejection (malformed, wrong structure, past-end).

**Key components:** BackingStore, OffsetPaginator, CursorPaginator, encode_cursor/decode_cursor, Page, PageResult, PaginationTestResult, PaginationReport, MockPaginationHandler

---

## 32. Accessibility (a11y) Test Harness

**File:** `harnesses/core/a11y_test_harness.py`
**Tests:** `tests/core/test_a11y_test_harness.py` — 127 tests
**Port:** 19180

Static WCAG-flavored accessibility checks on HTML, parsed with stdlib `html.parser` (no bs4/lxml). Checkers: AltTextChecker (missing/empty/redundant alt), LabelChecker (orphan inputs/selects/textareas lacking `<label for>` / aria-label), HeadingOrderChecker (skipped levels, multiple h1), AriaChecker (invalid roles, missing required aria-* attrs, aria-hidden on focusable), ContrastChecker (WCAG sRGB linearization + relative-luminance + contrast-ratio math from scratch, AA 4.5:1 / 3:1 thresholds, parses #rrggbb/#rgb/rgb()), LangChecker, LinkTextChecker (“click here”/empty), TableChecker (data table missing th/scope). Explicitly static-only — catches ~30–40% of real a11y issues, no browser/runtime DOM.

**Key components:** AltTextChecker, LabelChecker, HeadingOrderChecker, AriaChecker, ContrastChecker, LangChecker, LinkTextChecker, TableChecker, A11yIssue, A11yReport, MockA11yHandler

---

## 33. Agentic AI / Tool-Calling Test Harness

**File:** `harnesses/ai/agentic_test_harness.py`
**Tests:** `tests/ai/test_agentic_test_harness.py` — 109 tests
**Port:** 19190

Tests AI-agent control-flow and tool-use correctness — the top 2026 agent failure modes — using a deterministic scripted `MockAgent` (no real LLM). A `ToolRegistry` of `ToolSchema`s (required/optional args, types, enums, dangerous flag) backs the checks. ToolCallFidelityTester flags hallucinated tool names, missing required args, wrong arg types, unknown extra args, and out-of-enum values (reporting a fidelity ratio). RunawayLoopDetector catches non-termination via round caps and repeated-call signatures. MultiTurnStateTester verifies state set early is used later; ArgSchemaDriftTester catches prompt-tool mismatch when a schema changes; PlanVsExecutionTester detects skipped/reordered steps; UnsafeToolUseTester flags dangerous tool calls made without a guard.

**Key components:** ToolRegistry, ToolSchema, ToolCall, MockAgent, ToolCallFidelityTester, RunawayLoopDetector, MultiTurnStateTester, ArgSchemaDriftTester, PlanVsExecutionTester, UnsafeToolUseTester, AgentEvalReport, MockAgenticHandler

---

## 34. Supply-Chain / Build Reproducibility Test Harness

**File:** `harnesses/security/supplychain_test_harness.py`
**Tests:** `tests/security/test_supplychain_test_harness.py` — 156 tests
**Port:** 19200

Tests dependency and build integrity against a mock package registry. PinningChecker flags floating/wildcard version specifiers; IntegrityChecker verifies artifact sha256 against the lockfile with constant-time compare and rejects tampered artifacts; LockfileDriftChecker detects manifest-vs-lock divergence; NonexistentPackageChecker catches hallucinated dependencies (the “slopsquatting” failure) and warns on Levenshtein-1 typosquats; ReproducibleBuildChecker builds the same inputs twice and detects nondeterminism (embedded timestamp); KnownVulnChecker matches locked versions against a mock advisory range; TransitiveDepChecker resolves the dep tree and finds pin gaps and phantom deps.

**Key components:** LockedDep, PinningChecker, IntegrityChecker, LockfileDriftChecker, NonexistentPackageChecker, ReproducibleBuildChecker, KnownVulnChecker, TransitiveDepChecker, SupplyChainReport, MockRegistryHandler

---

## 35. File Upload / Decompression-Bomb Test Harness

**File:** `harnesses/security/upload_test_harness.py`
**Tests:** `tests/security/test_upload_test_harness.py` — 113 tests
**Port:** 19210

Tests file-ingestion safety — a classic silent-DoS / type-confusion surface. MultipartParser parses `multipart/form-data` (multiple fields + file parts, boundary-in-content, missing trailing boundary, CRLF, empty parts, truncated body). DecompressionBombChecker decompresses gzip/zlib/zip under a hard output cap and max compression-ratio, rejecting bombs before memory exhaustion, with a nested-zip depth limit. ContentTypeSniffer compares declared Content-Type against magic bytes (PNG/GIF/PDF/ZIP/JPEG), enforces an allow-list and `nosniff`. SizeLimitChecker stops streaming reads early at the limit; FilenameSanitizer rejects path-traversal, null bytes, absolute paths, and Windows reserved names; PartialStreamTester detects truncated uploads.

**Key components:** MultipartParser, DecompressionBombChecker, ContentTypeSniffer, SizeLimitChecker, FilenameSanitizer, PartialStreamTester, UploadValidator, UploadPart, UploadResult, UploadReport, MockUploadHandler

---

## 36. App-Security Test Harness (SSRF / Deserialization / JWT / XXE)

**File:** `harnesses/security/appsec_test_harness.py`
**Tests:** `tests/security/test_appsec_test_harness.py` — 129 tests
**Port:** 19220

Complements the injection-focused Security harness #6 by covering the OWASP-heavy classes most over-represented in AI-generated code, each with a vulnerable-vs-hardened demonstration. SSRFChecker blocks private/loopback/link-local/metadata ranges and `file://`/`gopher://` via the `ipaddress` module; DeserializationChecker detects dangerous pickle opcodes, PyYAML `!!python/object`, and Java serialization magic by signature (never unpickling untrusted data; insecure deserialization is ~2.74× more common in AI code); JWTChecker catches `alg:none`, HS/RS algorithm confusion, expired `exp`, and `iss`/`aud` mismatch with `hmac`-verified HS256; OpenRedirectChecker, MassAssignmentChecker (allow-list binder), and XXEChecker (DOCTYPE/ENTITY detection without resolution) round it out.

**Key components:** SSRFChecker, DeserializationChecker, JWTChecker, OpenRedirectChecker, MassAssignmentChecker, XXEChecker, SecFinding, AppSecReport, MockAppSecHandler

---

## 37. Spaced-Repetition (SRS) Test Harness

**File:** `harnesses/pharmacy/srs_test_harness.py`
**Tests:** `tests/pharmacy/test_srs_test_harness.py` — 22 tests
**Port:** none (SQLite backend; no networked mock server)

Tests the SM-2 spaced-repetition algorithm used by the pharmacy PTCB-prep app: initial-state outputs, interval ladder (1→6→EF-scaled), incorrect-answer reset (interval=0, reps=0), ease floor at 1.3, monotonicity invariants (ease never decreases on correct, interval grows for reps≥2), convergence (20 correct cycles → interval > 100 days), ease upper bound (1000 correct → ease ≤ 105), junk-input fallback, and full DB round-trip (sm2_update → upsert → retrieve → values match). Also tests `calculate_weight`: overdue path (cap 50), not-yet-due path, monotone with increasing days_since, and legacy-NULL fallback. `MockMasteryStore` uses in-memory SQLite with thread-safe locking.

**Key components:** `_sm2_update`, `_weight_from_stats`, `MockMasteryStore`, `SRSSimulator`, `run_all_scenarios`, `build_parser`

---

## 38. Clinical Calculator Test Harness

**File:** `harnesses/pharmacy/clinical_calc_test_harness.py`
**Tests:** `tests/pharmacy/test_clinical_calc_test_harness.py` — 28 tests
**Port:** 19240

Tests medical calculators as a **patient-safety-critical** domain, with independent reference implementations as oracles. BSA Mosteller: formula identity verified to 2 dp, plausibility bounds [0.10, 4.00] m², strict monotonicity in height and weight. Cockcroft-Gault CrCl: formula identity, female factor exactly 0.85×, strictly decreasing with age and SCr, age boundary (141 raises ValueError). Peds dose: dimensional consistency both legs. Days supply: floor division convention (qty=31, daily=3 → 10). Insulin: priming-waste subtracted (F-06 fix), 3650-day cap. Cross-calculator: doubling weight increases both BSA and CrCl.

**Key components:** `_ref_bsa`, `_ref_crcl`, `calc_bsa_mosteller`, `calc_crcl`, `calc_peds_dose`, `calc_days_supply`, `calc_insulin_days`, `ClinicalCalcHandler`, `build_parser`

---

## 39. Temporal PIN Lockout Test Harness

**File:** `harnesses/pharmacy/lockout_test_harness.py`
**Tests:** `tests/pharmacy/test_lockout_test_harness.py` — 20 tests
**Port:** 19250

Tests time-windowed PIN lockout — a gap not covered by the AuthZ (RBAC) or Rate-Limiting (throughput) harnesses. `FakeClock` (injectable, `now()` + `advance()`) enables deterministic boundary testing without real sleeps. `LockoutManager` tracks `{username: {count, locked_since}}` with a `threading.Lock` and configurable `threshold`/`lockout_seconds`. Covers: first attempt never locked, THRESHOLD-1 still permitted, exact threshold locks, t=299s still locked (exclusive), t=300s released (inclusive), counter resets on expiry, successful attempt resets before threshold, per-user isolation (locking A does not affect B), ±1s boundary precision, `BuggyLockoutManager` (never unlocks) detected, configurable thresholds (1 and 5), and concurrent attempts → lockout fires exactly once. Mock HTTP server on 19250: `POST /login` → 200/401/423.

**Key components:** `FakeClock`, `LockoutManager`, `BuggyLockoutManager`, `BuggyLockoutManager2`, `LockoutHandler`, `run_all_scenarios`, `build_parser`

---

## 40. Backup / Restore Lifecycle Test Harness

**File:** `harnesses/pharmacy/backup_restore_test_harness.py`
**Tests:** `tests/pharmacy/test_backup_restore_test_harness.py` — 20 tests
**Port:** none (filesystem + SQLite; no networked mock server)

Tests SQLite online backup/restore lifecycle — a gap not covered by the generic DB harness which tests only CRUD/transactions. Uses `sqlite3.Connection.backup()` (WAL-safe, transactional). Scenarios: magic bytes at offset 0, backup immediately readable with all source tables, data-faithful row comparison, timestamp-pattern filename, `db_list_backups()` newest-first, full round-trip (snapshot → mutate → restore → mutation gone), atomic restore (live DB readable after), non-existent path raises `OperationalError`/`OSError`, empty (schema-only) DB, NULL columns survive intact, WAL mode does not corrupt, unlistable directory returns `[]` (no raise), corrupt `.db` bytes raise on restore without overwriting live DB. All paths in `tempfile.mkdtemp()` isolated directories.

**Key components:** `SQLITE_MAGIC`, `_make_test_db`, `_db_backup`, `_db_restore`, `_db_list_backups`, `BackupResult`, `run_all_scenarios`, `build_parser`

---

## 41. Rotating-Capped Audit Log Test Harness

**File:** `harnesses/pharmacy/auditlog_cap_test_harness.py`
**Tests:** `tests/pharmacy/test_auditlog_cap_test_harness.py` — 20 tests
**Port:** 19270

Tests a DB-backed ring-buffer audit log with a hard row cap — a gap not covered by the Logging harness (format/sensitive-data) or the DB harness (generic CRUD). `AuditLogStore` prunes with `DELETE FROM AuditLog WHERE id NOT IN (SELECT id ... ORDER BY id DESC LIMIT cap)`. Scenarios: single write, newest-first retrieval, no-premature-prune at cap-1, cap triggers at cap+1 (exactly cap rows remain), newest rows retained after prune, idempotent overflow (3×cap inserts → always exactly cap rows), `LowCapAuditLog` (cap=3) prunes at 4th insert, auto-increment IDs not reset after prune, text-filter correct after prune cycle, integration export (10 rows), export file header format (`Pharmacy Audit Log Export`, `Generated:`, `Total entries:`, `---`), concurrent writes (10 threads × 5 rows → ≤ cap, no missing rows), `BuggyAuditLog` (skips DELETE) detected. Mock HTTP server on 19270.

**Key components:** `AuditLogStore`, `BuggyAuditLogStore`, `LowCapAuditLog`, `AuditLogHandler`, `run_all_scenarios`, `build_parser`

---

## 42. Date-Window Expiry Alerting Test Harness

**File:** `harnesses/pharmacy/expiry_window_test_harness.py`
**Tests:** `tests/pharmacy/test_expiry_window_test_harness.py` — 22 tests
**Port:** 19280

Tests inventory expiration alerting — calendar boundary semantics combined with SQLite queries under a controllable clock. A gap distinct from the DateTime harness (pure date math) and DB harness (generic CRUD): the combined pattern of calendar arithmetic + parameterized SQL under an injected `today` string. `ExpiryStore` (in-memory SQLite): `expiring(within_days, today)` uses `<= cutoff` (inclusive); `expired(today)` uses `< today` (strictly exclusive). `DateWindowOracle` is the stdlib-only reference. Scenarios: today+30 in 30-day window (inclusive), today+31 NOT in window, already-expired in `expiring`, today NOT in `expired`, yesterday in `expired`, leap day 2024-02-29 as expired on 2024-03-01, month-end rollover (Jan 31 + 1 = Feb 01), year-end rollover (Dec 31 + 1 = Jan 01 next year), ASC sort by exp_date then name, empty result, `within_days=0`, 365-day scan, LIKE wildcard escape (`%`, `_`, `\` chars). Mock HTTP server on 19280.

**Key components:** `_like_escape`, `ExpiryStore`, `DateWindowOracle`, `ExpiryHandler`, `run_all_scenarios`, `build_parser`

---

## 43. Partial-Fill Two-Phase Ledger Test Harness

**File:** `harnesses/pharmacy/partial_fill_test_harness.py`
**Tests:** `tests/pharmacy/test_partial_fill_test_harness.py` — 20 tests
**Port:** 19290

Tests pharmacy partial dispensing — a domain-specific open→resolve two-phase lifecycle not covered by the Idempotency harness (which targets HTTP idempotency keys and response artifacts). `PartialFillStore` (in-memory SQLite, `threading.Lock`): `add()` returns id; `list_open()` filters `resolved=0` newest-first; `count_open()` counts only unresolved; `resolve(pid)` issues `UPDATE ... WHERE id=? AND resolved=0` and returns `rowcount > 0`. Scenarios: fields correct on open, resolved disappears from `list_open`, True on first resolve, False on second (idempotent), False on nonexistent id, count=5, count=3 after 2 resolved, newest-first ordering, resolving #2 of 3 leaves #1 and #3, `AuditCapture` logs exactly once on True, `qty_owed=99` persisted correctly, concurrent race (2 threads, exactly one True + one False). `BuggyPartialFillStore` (always True) and `BuggyPartialFillStore2` (no WHERE filter) prove both failure directions. Mock HTTP server on 19290: `POST /partials` → 201, `GET /partials` → 200, `POST /partials/{id}/resolve` → 200/409.

**Key components:** `PartialFillStore`, `BuggyPartialFillStore`, `BuggyPartialFillStore2`, `AuditCapture`, `PartialFillHandler`, `run_all_scenarios`, `build_parser`

---

## 44–53. Research pass (2026-05, batch 4)

| # | Name | File | Tests | Proof status |
|---|------|------|-------|--------------|
| 44 | Tracing / Distributed Context | `harnesses/core/tracing_test_harness.py` | `tests/core/test_tracing_test_harness.py` | Self-test + unittest |
| 45 | Queue / Job Processing | `harnesses/core/queue_test_harness.py` | `tests/core/test_queue_test_harness.py` | Self-test + unittest |
| 46 | Feature Flags / Rollout | `harnesses/core/feature_flag_test_harness.py` | `tests/core/test_feature_flag_test_harness.py` | Self-test + unittest |
| 47 | Schema Evolution / Migration | `harnesses/core/schema_migration_test_harness.py` | `tests/core/test_schema_migration_test_harness.py` | Self-test + unittest |
| 48 | Secrets / Config Hygiene | `harnesses/security/secrets_config_test_harness.py` | `tests/security/test_secrets_config_test_harness.py` | Self-test + unittest |
| 49 | Audit Log Integrity | `harnesses/security/audit_log_integrity_test_harness.py` | `tests/security/test_audit_log_integrity_test_harness.py` | Self-test + unittest |
| 50 | RAG / Retrieval Evaluation | `harnesses/ai/rag_eval_test_harness.py` | `tests/ai/test_rag_eval_test_harness.py` | Self-test + unittest |
| 51 | Prompt Injection / Tool Safety | `harnesses/ai/prompt_injection_test_harness.py` | `tests/ai/test_prompt_injection_test_harness.py` | Self-test + unittest |
| 52 | MCP Contract / Tool Schema | `harnesses/ai/mcp_contract_test_harness.py` | `tests/ai/test_mcp_contract_test_harness.py` | Self-test + unittest |
| 53 | Hermeticity / Test Isolation | `harnesses/core/hermeticity_test_harness.py` | `tests/core/test_hermeticity_test_harness.py` | Self-test + unittest |

All 10 batch-4 harnesses are stdlib-only, include paired unittest coverage, and run through the root harness sweep. They extend the base suite into tracing/correlation, async queue semantics, rollout safety, schema migration compatibility, secret scanning, audit-log tamper evidence, RAG hallucination/citation checks, prompt-injection/tool-safety controls, MCP schema/fidelity checks, and hermetic test isolation.

Batch-4 verification run: `python3 -m unittest test_tracing_test_harness test_queue_test_harness test_feature_flag_test_harness test_schema_migration_test_harness test_secrets_config_test_harness test_audit_log_integrity_test_harness test_rag_eval_test_harness test_prompt_injection_test_harness test_mcp_contract_test_harness test_hermeticity_test_harness` → 82 tests, green. A targeted `make selftest` equivalent over the 10 harness files was also green.

---

## 54–59. Batch 5 additions (2026-05)

| # | Name | File | Tests | Proof status |
|---|------|------|-------|--------------|
| 54 | Payment / Checkout Integrity | `harnesses/core/payment_checkout_test_harness.py` | `tests/core/test_payment_checkout_test_harness.py` | Self-test + unittest |
| 55 | GraphQL Contract | `harnesses/core/graphql_contract_test_harness.py` | `tests/core/test_graphql_contract_test_harness.py` | Self-test + unittest |
| 56 | Search Relevance / Ranking | `harnesses/ai/search_relevance_test_harness.py` | `tests/ai/test_search_relevance_test_harness.py` | Self-test + unittest |
| 57 | Privacy / Consent Enforcement | `harnesses/security/privacy_consent_test_harness.py` | `tests/security/test_privacy_consent_test_harness.py` | Self-test + unittest |
| 58 | Statistical RNG Oracle | `harnesses/core/statistical_rng_oracle_test_harness.py` | `tests/core/test_statistical_rng_oracle_test_harness.py` | Self-test + unittest |
| 59 | Reconciliation / Ledger Integrity | `harnesses/core/reconciliation_ledger_test_harness.py` | `tests/core/test_reconciliation_ledger_test_harness.py` | Self-test + unittest |

Batch 5 adds payment/checkout idempotency and rounding checks, GraphQL depth/complexity/authz contract checks, search-ranking/relevance sanity, privacy/consent enforcement, RNG statistical oracles, and ledger reconciliation. All remain pure stdlib and use deterministic local fixtures.

Batch 5 verification: `python3 -m unittest tests.core.test_payment_checkout_test_harness tests.core.test_graphql_contract_test_harness tests.ai.test_search_relevance_test_harness tests.security.test_privacy_consent_test_harness tests.core.test_statistical_rng_oracle_test_harness tests.core.test_reconciliation_ledger_test_harness` → 105 tests, green.

---

## 60–62. Batch 6 additions (2026-05-30)

| # | Name | File | Tests | Proof status |
|---|------|------|-------|--------------|
| 60 | Agent Eval Replay | `harnesses/ai/agent_eval_replay_harness.py` | `tests/ai/test_agent_eval_replay_harness.py` | Self-test + unittest |
| 61 | IoT Telemetry Boundary | `harnesses/core/iot_telemetry_test_harness.py` | `tests/core/test_iot_telemetry_test_harness.py` | Self-test + unittest |
| 62 | gRPC Contract | `harnesses/core/grpc_contract_test_harness.py` | `tests/core/test_grpc_contract_test_harness.py` | Self-test + unittest |

Batch 6 adds deterministic agent transcript replay checks, IoT payload bounds/unit/sequence validation, and gRPC/protobuf-compatible contract fixtures. All remain pure stdlib and paired with unit tests.

Batch 6 verification: `python3 -m unittest tests.ai.test_agent_eval_replay_harness tests.core.test_iot_telemetry_test_harness tests.core.test_grpc_contract_test_harness` → 50 tests, green.

---

## 63–67. Batch 7 additions (2026-05-31)

| # | Name | File | Tests | Proof status |
|---|------|------|-------|--------------|
| 63 | Browser / E2E Static | `harnesses/core/browser_e2e_static_test_harness.py` | `tests/core/test_browser_e2e_static_test_harness.py` | Self-test + unittest + proof |
| 64 | Drift Detection | `harnesses/ai/drift_detection_test_harness.py` | `tests/ai/test_drift_detection_test_harness.py` | Self-test + unittest + proof |
| 65 | Retrieval Ranking | `harnesses/ai/retrieval_ranking_test_harness.py` | `tests/ai/test_retrieval_ranking_test_harness.py` | Self-test + unittest + proof |
| 66 | Schema Contract | `harnesses/core/schema_contract_test_harness.py` | `tests/core/test_schema_contract_test_harness.py` | Self-test + unittest + proof |
| 67 | Data Quality | `harnesses/core/data_quality_test_harness.py` | `tests/core/test_data_quality_test_harness.py` | Self-test + unittest + proof |

Batch 7 adds the repo's first explicit proof tests for newly added harnesses. Each of #63–67 keeps the paired unittest file and adds `test_<name>_proof.py` for planted-bad detection. The browser/E2E harness stays static and stdlib-only by design; production porting should add real browser execution outside this repo.

Batch 7 verification: `python3 -m unittest tests.core.test_browser_e2e_static_test_harness tests.core.test_browser_e2e_static_proof tests.ai.test_drift_detection_test_harness tests.ai.test_drift_detection_proof tests.ai.test_retrieval_ranking_test_harness tests.ai.test_retrieval_ranking_proof tests.core.test_schema_contract_test_harness tests.core.test_schema_contract_proof tests.core.test_data_quality_test_harness tests.core.test_data_quality_proof` → 107 tests, green.

---

## 68–72. Batch 8 additions (2026-05-31)

| # | Name | File | Tests | Proof status |
|---|------|------|-------|--------------|
| 68 | CI Workflow Hardening | `harnesses/security/ci_workflow_hardening_test_harness.py` | `tests/security/test_ci_workflow_hardening_test_harness.py` | Self-test + unittest + proof |
| 69 | Diff Secret-Gate | `harnesses/security/diff_secret_gate_test_harness.py` | `tests/security/test_diff_secret_gate_test_harness.py` | Self-test + unittest + proof |
| 70 | Check-Digit Identifier | `harnesses/core/check_digit_identifier_test_harness.py` | `tests/core/test_check_digit_identifier_test_harness.py` | Self-test + unittest + proof |
| 71 | Lexical Date Canonicalization | `harnesses/core/lexical_date_canonicalization_test_harness.py` | `tests/core/test_lexical_date_canonicalization_test_harness.py` | Self-test + unittest + proof |
| 72 | PII Redaction | `harnesses/security/pii_redaction_test_harness.py` | `tests/security/test_pii_redaction_test_harness.py` | Self-test + unittest + proof |

Batch 8 adds the next pure-stdlib proof harnesses: CI workflow permission hardening, diff-secret scanning gates, check-digit validation, lexical date canonicalization, and deterministic PII redaction. Each includes a paired unittest plus a planted-bad proof test.

Batch 8 verification: `python3 -m unittest tests.security.test_ci_workflow_hardening_test_harness tests.security.test_ci_workflow_hardening_proof tests.security.test_diff_secret_gate_test_harness tests.security.test_diff_secret_gate_proof tests.core.test_check_digit_identifier_test_harness tests.core.test_check_digit_identifier_proof tests.core.test_lexical_date_canonicalization_test_harness tests.core.test_lexical_date_canonicalization_proof tests.security.test_pii_redaction_test_harness tests.security.test_pii_redaction_proof` → 83 tests, green.

---

## 73. Circuit Breaker TEETH Harness

**File:** `harnesses/core/circuitbreaker_test_harness.py`
**Tests:** `tests/core/test_circuitbreaker_test_harness.py`, `tests/core/test_circuitbreaker_proof.py`
**Port:** 19330
**Proof status:** required TEETH — correct oracle clean; planted mutants caught; corpus nonempty

Defines a focused circuit-breaker contract: after `threshold` consecutive failures, the breaker enters `OPEN`; after `recovery_timeout`, the next allowed call is `HALF_OPEN`; a successful half-open probe closes and resets failure count; a failed half-open probe reopens; open calls are rejected without invoking the protected function.

The proof test uses planted mutants for never-opening, boundary off-by-one, half-open bypass, and failure-count reset behavior so a green test must show the correct oracle stays clean and mutants are caught.

---

## 74. JWT (HS256) Verification TEETH Harness

**File:** `harnesses/security/jwt_test_harness.py`
**Tests:** `tests/security/test_jwt_test_harness.py`, `tests/security/test_jwt_proof.py`
**Port:** 19400
**Proof status:** required TEETH — correct oracle clean; planted mutants caught; corpus nonempty

Defines a focused HS256 JWT verification contract: reject `alg=none`; reject tampered payloads; reject expired tokens; require issuer and audience matches; verify the HMAC signature against the expected secret; report failure reasons without leaking secrets.

The proof test uses planted mutants for alg-none acceptance, tamper acceptance, expiry skip, issuer/audience skip, and wrong-secret acceptance so a green test must show the correct oracle stays clean and mutants are caught.

---

## 75. PII / PHI Redaction TEETH Harness

**File:** `harnesses/security/pii_redaction_test_harness.py`
**Tests:** `tests/security/test_pii_redaction_test_harness.py`, `tests/security/test_pii_redaction_proof.py`
**Port:** 19410
**Proof status:** required TEETH — correct oracle clean; planted mutants caught; corpus nonempty

Defines a deterministic redaction contract for SSN, email, phone, MRN, DOB, and patient-name patterns using only local fixtures. The harness must redact sensitive spans, preserve non-sensitive surrounding text, avoid leaking raw sensitive values in report output, and avoid over-redacting unrelated safe text.

The proof test uses planted mutants for SSN leaks, email leaks, DOB leaks, patient-name leaks, and over-redaction so a green test must show the correct oracle stays clean and mutants are caught.

---

## 76. RAG Evaluation TEETH Harness

**File:** `harnesses/ai/rag_eval_test_harness.py`
**Tests:** `tests/ai/test_rag_eval_test_harness.py`, `tests/ai/test_rag_eval_proof.py`
**Port:** none (in-process oracle)
**Proof status:** required TEETH — correct oracle clean; planted mutants caught; corpus nonempty

Defines a deterministic retrieval-augmented-generation evaluation contract with a frozen local evidence corpus. The harness checks answer support against cited document ids, rejects fabricated citations, rejects unsupported claims, catches missing citations, and enforces citation precision.

The proof test uses planted mutants for fabricated citations, unsupported claims, missing citations, and wrong-document support so a green test must show the correct oracle stays clean and mutants are caught.

---

## 77. CWE / KEV Regression TEETH Harness

**File:** `harnesses/security/cwe_kev_regression_test_harness.py`
**Tests:** `tests/security/test_cwe_kev_regression_test_harness.py`, `tests/security/test_cwe_kev_regression_proof.py`
**Port:** none (in-process oracle)
**Proof status:** required TEETH — correct oracle clean; planted mutants caught; corpus nonempty

Defines a small local vulnerability-regression corpus that maps CWE-style failure classes and KEV-style pressure into deterministic examples. The harness verifies path traversal rejection, command injection rejection, unsafe deserialization rejection, weak hashing rejection, SSRF target blocking, and SQL injection rejection.

The proof test uses planted mutants for each failure class so a green test must show the correct oracle stays clean and mutants are caught.

---

## Current Port Map

| Port  | Harness                         |
|-------|---------------------------------|
| 8080  | Stress                          |
| 18900 | API / REST                      |
| 18910 | Web Scraper                     |
| 18920 | Security                        |
| 18930 | Chaos                           |
| 18940 | Memory                          |
| 18950 | Concurrency                     |
| 18960 | Fuzz                            |
| 18970 | Property-Based                  |
| 18980 | Mutation                        |
| 18990 | Regression Snapshot             |
| 19000 | Contract / Interface            |
| 19010 | Serialization                   |
| 19020 | Configuration                  |
| 19030 | Logging / Observability        |
| 19040 | Network / Protocol             |
| 19050 | Data Pipeline / ETL            |
| 19060 | Time / DateTime                |
| 19070 | Idempotency / Retry            |
| 19080 | State Machine                  |
| 19090 | Numeric / Money                |
| 19100 | Authorization                  |
| 19110 | LLM / AI-Feature Eval          |
| 19120 | Error-Path / Negative          |
| 19130 | Caching Correctness            |
| 19140 | Rate Limiting                  |
| 19150 | Webhook Delivery               |
| 19160 | i18n / Unicode                 |
| 19170 | Pagination / Cursor            |
| 19180 | Accessibility (a11y)           |
| 19190 | Agentic AI / Tools             |
| 19200 | Supply-Chain / Repro           |
| 19210 | File Upload / Bomb             |
| 19220 | App-Security                   |
| 19240 | Clinical Calculators           |
| 19250 | Temporal PIN Lockout           |
| 19270 | Rotating Audit Log             |
| 19280 | Date-Window Expiry             |
| 19290 | Partial-Fill Ledger            |
| 19300 | Payments / Checkout (reserved; in-process) |
| 19310 | GraphQL Contract (reserved; in-process)    |
| 19320 | Search Relevance (reserved; in-process)    |
| 19330 | Circuit Breaker                            |
| 19400 | JWT (HS256) Verification                   |
| 19410 | PII / PHI Redaction                        |
| —     | Database, CLI, SRS, Backup-Restore, Expiry-Window, Tracing, Queue, RAG Eval, the #44–53 research-pass harnesses, all of batch 6 (Agent Eval, IoT Telemetry, gRPC Contract, Browser/E2E, Drift Detection), batch 7, and batch 8 (CI Workflow Hardening, Diff Secret-Gate, Check-Digit Identifier, Lexical Date Canonicalization) — no networked mock server (SQLite / subprocess / in-process oracle) |

## Running Everything

```bash
make test          # python -m unittest discover -s tests -t . -p "test_*.py"
make selftest      # run --self-test for every harness
make proof         # run --self-test plus proof audit for every harness
make report        # regenerate STATUS.md and STATUS.json
```

**77 harnesses.** Live per-harness self-test and proof status is in `STATUS.md`
and `STATUS.json` (auto-generated by `make report`). The earlier `pharmacy/srs`, `core/hermeticity`,
and Windows portability (`prompt_injection`/`partial_fill`/`memory`) issues were
resolved in the 2026-05-29 fix-status pass (see `HARNESS_ROADMAP.md` →
"Resolved 2026-05-29"); any remaining per-harness gaps are tracked there.
Dice Duel is isolated under `dice_duel_lab/` and runs via its own lab sweep
instead of root discovery.

> Note: a full `discover` run is slow because the soak/stress/memory harnesses
> use real-time waits. Each batch is verified green on its own:
> - Batch 1 (#25–30, 713 tests): `python3 -m unittest test_llm_eval_test_harness
>   test_errorpath_test_harness test_cache_test_harness test_ratelimit_test_harness
>   test_webhook_test_harness test_i18n_test_harness` (≈8s).
> - Batch 2 (#31–36, 761 tests): `python3 -m unittest test_pagination_test_harness
>   test_a11y_test_harness test_agentic_test_harness test_supplychain_test_harness
>   test_upload_test_harness test_appsec_test_harness` (≈6s).
> - Batch 3 (#37–43, 152 tests): `python3 -m unittest test_srs_test_harness
>   test_clinical_calc_test_harness test_lockout_test_harness
>   test_backup_restore_test_harness test_auditlog_cap_test_harness
>   test_expiry_window_test_harness test_partial_fill_test_harness` (≈3s).
>
> All new harnesses are stdlib-only and each `--self-test` exits 0.

---

## Root Utility Scripts

These root-level scripts keep the generic harness suite separate from the Dice
Duel lab:

- `tools/check_no_dice_bleed.py` — fails if Dice Duel lab files reappear in the harness root.
- `tools/check_harness_inventory.py` — checks root harness test/implementation pairing.
- `tools/check_port_map.py` — checks documented numeric ports for duplicates.
- `tools/run_harness_sweep.py` — runs only the generic root harness tests.

Dice Duel lab utilities live inside `dice_duel_lab/`.
