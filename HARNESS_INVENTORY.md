# Test Harness Inventory

**Total: 92 harnesses.** Per-harness self-test and proof status is generated locally by
`make report` and uploaded by CI as a workflow artifact. Entries #1–43 are
documented in full below; #44–53 are bridged in a compact table; #54–59,
#60–62, #63–67, #68–72, #73, and #74–77 follow in full. As of the `fix-status-green` work every
harness passes `--self-test`; the formerly-failing `srs`/`hermeticity` bugs
and the Windows portability gaps are resolved.

`STATUS.md` is generated output, not the canonical source of truth. The source
of truth is the harness code, paired unittest suites, and current CI/test
output.
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

**File:** `harnesses/core/stress_test_harness.py` (979 lines)
**Tests:** `tests/core/test_stress_test_harness.py` — 53 tests
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

Ten harnesses added against 2026 bug-research sources. Full prose entries are
pending; one-line summaries below (see `HARNESS_ROADMAP.md` -> "Batch 4" for the
source signal behind each, and generated `STATUS.md` / `STATUS.json` for live self-test and proof status).

| # | File | Summary |
|---|---|---|
| 44 | `harnesses/core/null_propagation_test_harness.py` | None propagating through a call chain (the #1 AI-coded bug class); flags never-guarded None paths. |
| 45 | `harnesses/core/error_path_leak_test_harness.py` | Resource acquire/release leaks on error/exception paths; double-release and unbalanced-cleanup detection. |
| 46 | `harnesses/core/feature_flag_test_harness.py` | Flag flips mid-call, rollout/kill-switch consistency (the Google June-2025 outage class). |
| 47 | `harnesses/core/clock_skew_test_harness.py` | Distributed-time bugs: NTP jumps, monotonic regression, cross-node skew vs TTL/LWW merges. |
| 48 | `harnesses/core/schema_evolution_test_harness.py` | Reader/writer schema drift; backward/forward compatibility (silent pipeline schema drift). |
| 49 | `harnesses/core/dormant_code_test_harness.py` | Untriggered branches that crash on first hit; surfaces dormant-path crashes via synthetic inputs. |
| 50 | `harnesses/core/cardinality_test_harness.py` | Metric/label/cache-key cardinality explosion (bounded vs unbounded). |
| 51 | `harnesses/core/hermeticity_test_harness.py` | Hidden test dependencies on HOME/env/network/time (top flaky-test class). |
| 52 | `harnesses/pharmacy/drug_interaction_test_harness.py` | Drug–drug interaction checks and contraindication overridability. |
| 53 | `harnesses/ai/prompt_injection_test_harness.py` | OWASP LLM01 injection corpus + system-prompt-leak guard, scored by precision/recall floors. |

---

## 54. Distributed Tracing Test Harness

**File:** `harnesses/core/tracing_test_harness.py`
**Tests:** `tests/core/test_tracing_test_harness.py` — 19 tests
**Port:** none (in-process)

Validates a span set against an oracle and proves it catches a battery of broken traces — the failure modes LLM-written OpenTelemetry glue produces. Strict W3C `traceparent` parse/format (2-32-16-2 lower hex; all-zero trace/span IDs and version `ff` rejected). `validate_trace` checks single-root, no orphan (unresolved parent) or cross-trace parents, no parent-chain cycles, non-negative span durations, head-sampling consistency (a sampled child under an unsampled parent is flagged), required-attribute schema, and clock-skew tolerance (child start before parent beyond a bound). Seven `BUGGY_TRACES` fixtures each flip exactly their target counter; a `Propagator` round-trips context while a `BuggyPropagator` drops it. 22 self-test scenarios.

**Key components:** `TraceParent`, `Span`, `TraceConfig`, `TraceReport`, `validate_trace`, `Propagator`, `BuggyPropagator`, `BUGGY_TRACES`, `valid_trace`

---

## 55. Message-Queue Delivery Test Harness

**File:** `harnesses/core/queue_test_harness.py`
**Tests:** `tests/core/test_queue_test_harness.py` — 17 tests
**Port:** none (in-process)

Tests broker delivery semantics with an injectable clock. `InMemoryBroker` (oracle) covers at-least-once redelivery (nack and ack-timeout), exactly-once dedup that survives redelivery, DLQ routing after max deliveries, per-key FIFO via head-of-key delivery, consumer-group rebalance (no loss / no double-delivery of acked / order preserved), ack-timeout heartbeat extension, and backpressure (in-flight cap + publish-depth reject). Four buggy brokers are each proven caught: `NaiveBroker` (acks on poll → crash loses the message), `LossyExactlyOnce` (no dedup → double-process), `OrderBreakingRebalance` (delivers non-head-of-key), `NoDlqBroker` (never routes poison → loops forever). 20 self-test scenarios. Distinct from `idempotency` (the dedup primitive) and `concurrency` (race detection).

**Key components:** `Message`, `QueueConfig`, `Delivery`, `Clock`, `InMemoryBroker`, `NaiveBroker`, `LossyExactlyOnce`, `OrderBreakingRebalance`, `NoDlqBroker`, `DeliveryReport`, `consume_all`, `build_report`

---

## 56. Search-Relevance Test Harness

**File:** `harnesses/core/search_relevance_test_harness.py`
**Tests:** `tests/core/test_search_relevance_test_harness.py` — 25 tests
**Port:** 19320 (reserved; oracle runs in-process)

Classic IR ranking metrics over fixed graded judgment sets plus an analyzer corner-case oracle. Computes recall@k, precision@k, MRR, and NDCG (graded gain `2^grade-1`). A stdlib analyzer applies NFKC, casefold, accent-fold, naive plural-stem, stop-word drop, and CJK no-space segmentation, checked against an 8-case oracle. A lexical retriever (distinct-overlap, stable tie-break) over an engineered 20-doc / 6-query corpus lets the oracle meet recall ≥ 0.80 / MRR ≥ 0.70 / NDCG ≥ 0.80; a `reversed_search` ranker falls below the NDCG floor and a `no_fold_analyze` analyzer fails the fold cases. 22 self-test scenarios. Distinct from `llm_eval`/`rag_eval` (LLM answer quality) and `pagination` (cursor consistency).

**Key components:** `Doc`, `Judgment`, `QuerySet`, `SearchConfig`, `analyze`, `no_fold_analyze`, `search`, `reversed_search`, `recall_at_k`, `precision_at_k`, `mrr`, `ndcg_at_k`, `evaluate`, `RelevanceReport`

---

## 57. RAG Eval Test Harness

**File:** `harnesses/ai/rag_eval_test_harness.py`
**Tests:** `tests/ai/test_rag_eval_test_harness.py` (+ `_proof.py`) — 24 tests
**Port:** none (in-process)

Scores retrieval-augmented answers on four axes RAG fails silently on: retrieval recall@k, citation faithfulness (a citation counts only if it was retrieved AND its passage supports a claim), answer grounding (claims present in the post-overflow context), and context-window overflow (greedy-pack drop of tail passages). A deterministic lexical retriever over an engineered 20-passage / 6-case corpus lets the oracle meet recall ≥ 0.80 / faithfulness ≥ 0.90 / grounding ≥ 0.80. The current TEETH proof checks frozen recall-floor, fabricated-citation, overflow-grounding, and zero-recall cases; planted auditors catch keyword-only retrieval, citation-fabrication blindness, overflow blindness, and invented hits for empty retrieval. 19 self-test scenarios. Distinct from `ai/llm_eval` (answer graders, no retrieval) and `ai/prompt_injection` (safety corpus).

**Key components:** `Passage`, `RagCase`, `RagConfig`, `retrieve`, `keyword_only_retrieve`, `truncating_retrieve`, `recall_at_k`, `citation_audit`, `grounding_audit`, `context_overflow`, `evaluate`, `RagReport`, `RAG_AUDIT_CORPUS`, `oracle_rag_audit`, `TEETH`

---

## 58. GraphQL Contract Test Harness

**File:** `harnesses/core/graphql_test_harness.py`
**Tests:** `tests/core/test_graphql_test_harness.py` — 28 tests
**Port:** 19310 (reserved; oracle runs in-process)

Parses queries into an AST (selection sets, aliases, fragment spreads, arg-skipping) and runs analyzers: schema-vs-resolver coverage (missing/orphan), query depth, query cost (list fields multiply, nesting compounds, aliases counted), N+1 list-resolver detection (with a dataloader-batched exclusion), fragment-cycle detection (direct and indirect), and unknown field/fragment validation. `enforce_limits` rejects every abusive query (deeply nested, cost-bomb, wide-alias amplification) while a `LeakyResolverSet` (missing + orphan) and a naive no-limit executor are caught. 21 self-test scenarios. Distinct from `core/contract` (arbitrary-callable pre/post/invariants) and `core/api` (REST CRUD).

**Key components:** `Schema`, `FieldDef`, `FragmentDef`, `Selection`, `parse_query`, `schema_resolver_coverage`, `query_depth`, `query_cost`, `detect_n_plus_one`, `fragment_cycles`, `enforce_limits`, `GraphQLReport`, `audit`

---

## 59. Payments / Checkout Test Harness

**File:** `harnesses/core/payments_test_harness.py`
**Tests:** `tests/core/test_payments_test_harness.py` — 25 tests
**Port:** 19300 (reserved; oracle runs in-process)

Composes a Decimal `Money` (banker's rounding + exact largest-remainder `allocate`), a payment state machine with money guards, and an idempotency-key replay contract. The oracle enforces Σcaptures ≤ authorized, Σrefunds ≤ captured, currency match, and minor-unit precision (USD 2 / JPY 0 / BHD 3); a decline taxonomy classifies soft/hard/fraud + retryable; a 3DS challenge blocks capture until resolved. Five buggy processors each break a money invariant and are caught: overcapture, double-refund, float-drift reconciliation, idempotency-ignoring replay (double charge), and challenge-is-success (captures an unverified 3DS charge). 27 self-test scenarios. Reuses the *patterns* of `numeric`/`statemachine`/`idempotency` but is self-contained (no imports), with the composition as the novel surface.

**Key components:** `Money`, `Currency`, `DeclineCode`, `PaymentState`, `PaymentProcessor`, `OvercaptureProcessor`, `DoubleRefundProcessor`, `FloatProcessor`, `ReplayChargesTwiceProcessor`, `ChallengeIsSuccessProcessor`, `LedgerReport`, `classify_decline`

---

## 60. Circuit Breaker Resilience Test Harness

**File:** `harnesses/core/circuitbreaker_test_harness.py`
**Tests:** `tests/core/test_circuitbreaker_test_harness.py` (+ `_proof.py`) — 20 tests
**Port:** 19330

Tests the circuit-breaker pattern under an injectable `FakeClock`: CLOSED → OPEN
on a failure threshold, OPEN rejects calls fast (`CircuitOpenError`), OPEN →
HALF_OPEN after a reset timeout, HALF_OPEN → CLOSED on a probe success / → OPEN
on probe failure. `CircuitBreakerOracle` is the reference state model the live
`CircuitBreaker` is checked against. The current TEETH proof replays frozen
event logs for threshold trips, success resets, half-open close/retrip, probe
caps, and pre-timeout open rejection; planted auditors cover late-open,
reset-blind, half-open-failure-closing, cap-ignoring, and open-window bugs.
13 self-test scenarios. Ported from the batch-4 resilience branch into the reorg
(port reassigned from 19300 to avoid colliding with `core/payments`).

**Key components:** `CircuitBreaker`, `CircuitBreakerOracle`, `FakeClock`, `CircuitOpenError`, `CircuitHandler`, `CBTestResult`, `start_mock_server`, `CIRCUIT_BREAKER_AUDIT_CORPUS`, `oracle_circuitbreaker_audit`, `TEETH`

---

## 61. JWT (HS256) Verification Test Harness

**File:** `harnesses/security/jwt_test_harness.py`
**Tests:** `tests/security/test_jwt_test_harness.py` (+ `_proof.py`) — 21 tests
**Port:** 19400

Tests JWT encode and — more importantly — *verification* against the classic
auth-bypass attacks: `alg=none` acceptance, HS/RS algorithm confusion,
signature stripping/forgery, and expiry handling, using stdlib `hmac`/`hashlib`.
`VerifyResult` reports pass/fail with a reason string. The current TEETH proof
checks valid-token, `alg=none`, algorithm allow-list, signature tamper,
expiration, and required-claim cases; planted auditors cover alg-none acceptance,
allow-list bypass, signature blindness, time-claim blindness, and required-claim
blindness. 14 self-test scenarios. Ported from the batch-4 branch (port
reassigned 19320 → 19400). Complements the injection-focused `security/security`
and `security/appsec` harnesses.

**Key components:** `encode`, `verify`, `VerifyResult`, `JwtHandler`, `JwtTestResult`, `start_mock_server`, `JWT_AUDIT_CORPUS`, `oracle_jwt_audit`, `TEETH`

---

## 62. PII / PHI Redaction Test Harness

**File:** `harnesses/security/pii_redaction_test_harness.py`
**Tests:** `tests/security/test_pii_redaction_test_harness.py` (+ `_proof.py`) — 23 tests
**Port:** 19410

Tests detection + redaction of PII/PHI (emails, phone numbers, SSNs, card
numbers, etc.) via stdlib `re` detectors, scored against a `RedactionOracle`
with precision/recall over a labelled corpus (catches both under-redaction
leaks and over-redaction false positives). The current TEETH proof checks mixed
entity counts, raw-secret removal, digit-run removal, safe-number over-redaction
guards, ZIP preservation, mask-mode SSN behavior, and idempotency; planted
auditors cover SSN blindness, digit leakage, Luhn-blind over-redaction, and
non-idempotent redaction. 14 self-test scenarios. Ported from the batch-4 branch
(port reassigned 19310 → 19410).

**Key components:** `Redactor`, `RedactionOracle`, `RedactionHandler`, `RedactTestResult`, `start_mock_server`, `PII_AUDIT_CORPUS`, `oracle_pii_audit`, `TEETH`

---

## 63. Multi-Turn Agent Eval Test Harness

**File:** `harnesses/ai/agent_eval_test_harness.py`
**Tests:** `tests/ai/test_agent_eval_test_harness.py` — 20 tests
**Port:** none (in-process)

Scores fixed scripted multi-turn agent transcripts against annotated goal states and a mock tool schema — the failure modes single-turn graders miss. The oracle checks task completion (final state == goal), tool-call validity (known name + required args + arg types), hallucinated-tool detection, error recovery (a tool error must be followed by a valid retry or escalation, not a fabricated claim), looping (no-progress repeat rate), instruction retention (an early `forbid:` constraint obeyed through later turns), premature-success claims, and unsafe actions (a dangerous tool called without confirmation). Four good transcripts meet all floors; six bad ones each trip one invariant. Seven buggy graders — claim-trusting, name-only validity, no-hallucination-check, recovery-blind, loop-ignoring, constraint-amnesiac, confirmation-blind — each miss one failure class the oracle catches, via injected scoring functions. Floors: resolved ≥ 0.90, validity ≥ 0.95, recovery ≥ 0.90, retention ≥ 0.95, loop ≤ 0.20; zero hallucinated/premature/unsafe. 23 self-test scenarios. Distinct from `ai/agentic` (single-turn server-style tool-call fidelity).

**Key components:** `ToolSig`, `ToolCall`, `ToolResult`, `Turn`, `Transcript`, `AgentEvalConfig`, `AgentEvalReport`, `evaluate`, `GOOD_TRANSCRIPTS`, `BAD_TRANSCRIPTS`

---

## 64. IoT / Telemetry Ingest Test Harness

**File:** `harnesses/core/iot_telemetry_test_harness.py`
**Tests:** `tests/core/test_iot_telemetry_test_harness.py` — 22 tests
**Port:** none (in-process)

Models an MQTT-like telemetry ingest path as pure data with an injectable `FakeClock` for server-ingest time. The oracle `ingest()` enforces QoS semantics (QoS-2 exactly-once, QoS-1 at-least-once-deduped, QoS-0 best-effort), per-topic re-sequencing by `seq` (final order strictly increasing), idempotency-key dedupe, clock-skew handling (flag > 60s, reject > 1h, canonical timestamp = server ingest, skewed event-time excluded from windowing), watermark/allowed-lateness windowing, retained-latest-only, persistent-session replay on reconnect, and last-will on abnormal disconnect. Eight buggy ingesters each break one invariant and are caught: QoS-2 at-least-once, no-resequence, no-dedupe, device-clock-truster, non-persistent-session, retain-all, no-watermark, no-last-will. 24 self-test scenarios. Distinct from `core/queue` (broker delivery) and `core/clock_skew` (TTL/LWW).

**Key components:** `Message`, `Record`, `DeviceSession`, `IotReport`, `IngestResult`, `IotConfig`, `ingest`, `reconnect`, `on_disconnect`, `STREAM`, `SESSIONS`

---

## 65. gRPC / Proto Contract Test Harness

**File:** `harnesses/core/grpc_contract_test_harness.py`
**Tests:** `tests/core/test_grpc_contract_test_harness.py` — 26 tests
**Port:** none (in-process)

Models protos and a mock gRPC service as pure data (no grpc/protobuf libs) and audits the contract rules LLM-written glue breaks. The oracle enforces proto-evolution safety (reserve removed field numbers, no number reuse, no wire-type change on kept fields), open-vs-closed enum unknown-value handling, deadline propagation (downstream ≤ original − elapsed, ±5 ms via a `MsClock`), streaming half-close (handler stops emitting after CloseSend), status-code correctness (RESOURCE_EXHAUSTED vs PERMISSION_DENIED across the 17 canonical codes), metadata propagation (`x-request-id` survives a hop), send/recv size-limit symmetry, and unary idempotency (exactly one side effect on retry). Nine buggy implementations each break one rule via injected components, surfaced as per-class violation counters with a `meets_contract()` predicate. 23 self-test scenarios.

**Key components:** `FieldDescriptor`, `MessageDescriptor`, `EnumDescriptor`, `RpcSpec`, `WireField`, `STATUS_CODES`, `validate_evolution`, `roundtrip`, `audit`, `GrpcReport`, `MsClock`

---

## 66. Browser / E2E Surrogate Test Harness

**File:** `harnesses/core/browser_e2e_test_harness.py`
**Tests:** `tests/core/test_browser_e2e_test_harness.py` — 23 tests
**Port:** none (in-process)

A deterministic DOM/E2E surrogate (no real browser, no asyncio): the DOM is immutable data, re-render is a pure tree mutation (`apply_mutation`), and async work is a manually-drained FIFO `EventLoop`. The oracle re-resolves selectors against the current DOM before clicking (no stale handle), asserts only after the loop settles, enforces event order (focus < input, change < click), raises `UnmockedRequestError` on unmocked requests, detects hydration structural mismatches (server vs client preorder), and prefers role/testid selectors over brittle absolute XPath. Six buggy implementations reproduce one flake each: stale-handle clicker, eager asserter, reordered event emitter, silent-404 fetch, hydration-blind renderer, brittle-XPath selector. 22 self-test scenarios. Complements `core/a11y` (accessibility tree) without overlapping it.

**Key components:** `Node`, `Dom`, `Selector`, `MockResponse`, `EventLoop`, `UnmockedRequestError`, `PrematureAssertionError`, `apply_mutation`, `resolve`, `hydration_diff`, `audit`, `E2EReport`

---

## 67. Model / Embedding Drift Detection Test Harness

**File:** `harnesses/ai/drift_detection_test_harness.py`
**Tests:** `tests/ai/test_drift_detection_test_harness.py` — 17 tests
**Port:** none (in-process)

Computes drift metrics by hand over fixed float fixtures (no numpy): PSI with an epsilon floor, KL and Jensen-Shannon divergence, Hellinger distance, embedding-centroid Euclidean displacement, query-document cosine-similarity drop, Spearman rank correlation of top-k neighbors, neighborhood churn, and query/index model-version mismatch. The oracle trips every alert on a planted-drift case and stays silent on a stable case. Seven buggy detectors each miss real drift or false-alarm on stable data: PSI with no epsilon floor (drops empty-bin terms), KL with swapped arguments, an averaged (washed-out) centroid distance, an unnormalized cosine, set overlap in place of a rank correlation, version-blind, and a stable-data false alarmer. Thresholds: PSI > 0.25, KL/JS > 0.20, Hellinger > 0.30, centroid > 0.50, cosine drop > 0.10, Spearman < 0.70, churn > 0.20. 24 self-test scenarios. Distinct from `ai/rag_eval` (retrieval/citation quality) and `ai/llm_eval` (answer graders).

**Key components:** `DriftCase`, `DriftReport`, `psi`, `kl_div`, `js_div`, `hellinger`, `centroid_distance`, `cosine_mean_drop`, `spearman`, `neighborhood_churn`, `compute_drift`

---

## Batch 7. Proof-backed Security, Agent, and Game Harnesses

| # | File | Tests | Proof |
|---|---|---|---|
| 68 | `harnesses/security/cwe_kev_regression_test_harness.py` | `tests/security/test_cwe_kev_regression_test_harness.py` | `tests/security/test_cwe_kev_regression_proof.py` |
| 69 | `harnesses/ai/agent_memory_context_test_harness.py` | `tests/ai/test_agent_memory_context_test_harness.py` | `tests/ai/test_agent_memory_context_proof.py` |
| 70 | `harnesses/core/game_loop_simulation_test_harness.py` | `tests/core/test_game_loop_simulation_test_harness.py` | `tests/core/test_game_loop_simulation_proof.py` |
| 71 | `harnesses/core/statistical_rng_oracle_test_harness.py` | `tests/core/test_statistical_rng_oracle_test_harness.py` | `tests/core/test_statistical_rng_oracle_proof.py` |
| 72 | `harnesses/core/canvas_scene_state_test_harness.py` | `tests/core/test_canvas_scene_state_test_harness.py` | `tests/core/test_canvas_scene_state_proof.py` |

Batch 7 makes proof explicit: each harness keeps a deterministic safe fixture and a planted bad fixture. The paired unit test verifies the API/CLI; the proof test verifies the bad fixture is actually rejected.

---

## 73. Complexity / Bloat Test Harness

**File:** `harnesses/core/complexity_test_harness.py`
**Tests:** `tests/core/test_complexity_test_harness.py` (+ `_proof.py`) — 28 tests
**Port:** none (in-process)

Flags code bloat and maintainability regressions in AI-generated or human code. Computes cyclomatic complexity, cognitive complexity, function length, parameter count, and nesting depth with stdlib `ast`, then gates files or directories against configured thresholds. The self-test pins hand-computed metric values, proves a bad high-complexity fixture is flagged, and proves clean code passes. The current TEETH proof checks clean code, cognitive/nesting breaches, length bloat, parameter-count bloat, and nested-vs-flat cognitive contrast; planted auditors cover cyclomatic-only, length-blind, params-blind, and nesting-blind behavior.

**Key components:** `Thresholds`, `FunctionMetrics`, `analyze_source`, `analyze_path`, `COMPLEXITY_AUDIT_CORPUS`, `oracle_complexity_audit`, `TEETH`

---

## 74. CI Workflow Hardening Test Harness

**File:** `harnesses/security/ci_workflow_hardening_test_harness.py`
**Tests:** `tests/security/test_ci_workflow_hardening_test_harness.py` (+ `_proof.py`) — 9 tests
**Port:** none (in-process; static audit of parsed workflow objects)

Audits GitHub Actions workflow definitions for the poisoned-pipeline / pwn-request class — a CI-config attack surface neither Supply-Chain (#34, dependency hashes/slopsquat) nor App-Security (#36) nor CWE/KEV (#68) covers. `audit_workflow` flags: action `uses` refs not pinned to a full 40-hex commit SHA, `pull_request_target` (including the YAML `on`→boolean-`True` key quirk), fork checkout via `with.ref: github.head_ref`, missing top-level `permissions`, missing per-job `timeout-minutes`, `actions/checkout` without `persist-credentials: false`, fork-PR scan skips (`head.repo.full_name == github.repository`), and ungated `concurrency`. Stdlib-only: rules run on already-parsed dict fixtures (no PyYAML); ported from `tools/control_audit.py`. A planted `audit_workflow_naive` that skips the SHA-pin check proves the action-pin rule has teeth.

**Key components:** `Finding`, `WorkflowCase`, `AuditResult`, `audit_workflow`, `audit_workflow_naive`, `ACTION` regex + 40-hex SHA pin check, `_events`, `_check_concurrency`

---

## 75. Check-Digit Identifier Test Harness

**File:** `harnesses/core/check_digit_identifier_test_harness.py`
**Tests:** `tests/core/test_check_digit_identifier_test_harness.py` (+ `_proof.py`) — 17 tests
**Port:** none (in-process)

Self-checking-identifier checksum oracles, generalized via `ChecksumSpec` + a `SCHEMES` registry (`validate(scheme, identifier)`): DEA (faithful port of pharmacy-app `verify_dea_logic` — 2 letters + payload digits + mod-10 weighted check, ASCII-only guard so Unicode digits like `٠` are rejected, the F-05 fix), Luhn (mod-10), and ISBN-10 (mod-11, `X` check). The headline oracle `single_digit_corruption_sweep` enumerates every single-digit substitution of a valid sample and asserts detection. Luhn and ISBN-10 detect 100% of single-digit errors; the DEA checksum provably cannot (a ±5 swap on a doubled position leaves the units-digit check unchanged), so the harness asserts DEA reproduces exactly its derived blind set (`dea_expected_escapes`) rather than hide the weakness. A `validate_naive` that checks only length/charset proves the check-digit step is load-bearing. Also surfaces the DEA prefix→prescriber class. Numeric/Money (#23) has no checksum logic.

**Key components:** `ChecksumSpec`, `SCHEMES`, `validate`, `validate_naive`, `dea_is_valid`, `luhn_is_valid`, `isbn10_is_valid`, `single_digit_corruption_sweep`, `dea_expected_escapes`, `dea_prescriber_class`, `DEA_PREFIX_PRESCRIBER`

---

## 76. Diff Secret-Gate Test Harness

**File:** `harnesses/security/diff_secret_gate_test_harness.py`
**Tests:** `tests/security/test_diff_secret_gate_test_harness.py` (+ `_proof.py`) — 13 tests
**Port:** none (in-process; parses a provided unified-diff string)

A unified-diff-aware secret scanner. The novel oracle is **direction-awareness**: `scan_diff` reports a secret only on ADDED (`+`) lines with correct post-change line numbers — a secret on a REMOVED (`-`) line (e.g. a key being rotated out) does not trip the gate. `scan_line` matches secret tokens (AWS `AKIA…`, GitHub `ghp_`/`github_pat_`, PEM private-key blocks, Slack `xox[baprs]`, Google `AIza…`, generic `secret/token/password =` assignments) with an `allowlist secret` escape hatch. **Scope is secret tokens only** — PII (EMAIL/SSN/PHONE/CREDIT) is owned by PII/PHI Redaction (#62) and deliberately not duplicated. Ported from `tools/scan_staged.py` (PII regexes omitted). The planted `scan_diff_naive` scans every content line regardless of `+`/`-` and over-reports on a removed-secret diff, proving the direction logic has teeth. All fixture secrets are built by concatenation so the file does not trip its own gate.

**Key components:** `SECRET_PATTERNS`, `scan_line`, `iter_added_lines`, `scan_diff`, `scan_diff_naive`, `DiffCase`, `DiffResult`, `run_case`

---

## 77. Lexical Date Canonicalization Test Harness

**File:** `harnesses/core/lexical_date_canonicalization_test_harness.py`
**Tests:** `tests/core/test_lexical_date_canonicalization_test_harness.py` (+ `_proof.py`) — 13 tests
**Port:** none (in-process)

Guards the data-corruption trap where a date string that parses fine but is not zero-padded silently breaks TEXT-column `ORDER BY` / range comparison: lexically `'2026-5-9' > '2026-10-01'`. Motivated by pharmacy-app `data.py` (`Inventory.exp_date TEXT NOT NULL`, `db_expired_inventory` does `WHERE exp_date < ? ORDER BY exp_date ASC`, and `_date_is_valid` accepts non-padded input via `strptime`). The headline invariant `lexical_matches_chronological`: for canonical dates, lexical sort == chronological sort; for a dataset containing a non-canonical string the two orders diverge, and `canonical_then_lexical_sort` restores agreement. `strict_is_valid` rejects parseable-but-non-canonical strings; the planted `lenient_is_valid` (mirroring the source `strptime` check) accepts `'2026-5-9'`, proving the strict rule is needed. Distinct from Time/DateTime (#20, round-trips only) and Date-Window Expiry (#42, calendar+SQL).

**Key components:** `canonicalize`, `is_canonical`, `strict_is_valid`, `lenient_is_valid`, `lexical_sort`, `chronological_sort`, `canonical_then_lexical_sort`, `lexical_matches_chronological`, `CanonCase`, `SortCase`, `DIVERGENT_DATES`

---

## Separate Lab: Dice Duel Reliability Lab

**Folder:** `dice_duel_lab/`
**App file:** `dice_duel_lab/dice_duel.py`
**Tests:** `dice_duel_lab/dice_duel_tests.py` — 124 test methods by static count
**Sweep:** `python3 dice_duel_lab/run_dice_duel_lab_sweep.py`

Dice Duel is no longer mixed into the generic harness root. It is a controlled
target used to develop reusable reliability/testing patterns: run-budget guards,
config validation before writes, manifest/hash/size verification, previous-good
generation recovery, raw-vs-self-heal crash probing, mutation probes, survivor
test repair, fight-result contracts, and summary invariants.

Current lab files are intentionally separated:

- `dice_duel_lab/probes/` — mutation probes 1–5 plus crash-interruption probe.
- `dice_duel_lab/patchers/` — one-shot patchers kept for traceability.
- `dice_duel_lab/artifacts/` — generated logs, manifests, and reconciliation output.
- `dice_duel_lab/archive/` — superseded backups, drafts, and older patchers.

The former known-failing win-rate assertion is resolved. The lab still does not
claim true OS/process-kill durability, `fsync()` / directory-sync durability,
production packaging, or CLI expansion.

---

## Port Map

| Port  | Harness                        |
|-------|--------------------------------|
| 8080  | Stress                         |
| 18900 | API / REST                     |
| 18910 | Web Scraper                    |
| 18920 | Security                       |
| 18930 | Chaos / Resilience             |
| 18940 | Memory / Soak                  |
| 18950 | Concurrency                    |
| 18960 | Fuzz                           |
| 18970 | Property-Based                 |
| 18980 | Mutation                       |
| 18990 | Regression & Snapshot          |
| 19000 | Contract / Interface           |
| 19010 | Serialization                  |
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

## Batch 10 — OWASP Top 10:2025 web + LLM harnesses (#78–92, shipped 2026-06-21)

Fifteen harnesses extending OWASP Top 10:2025 (web) and OWASP Top 10 for LLM
Applications 2025 coverage. All declare `TEETH` (required); each ships a paired
unittest and a planted-bad proof test.

**security/ — OWASP Top 10:2025 (web)**
- `crypto` — A04 Cryptographic Failures: password hashing, weak RNG, ECB/legacy ciphers, hard-coded secrets, TLS verification.
- `misconfig` — A02 Security Misconfiguration: debug mode, wildcard CORS, default creds, cookie flags, file permissions.
- `advanced_injection` — A05 Injection: SSTI, NoSQL, LDAP.
- `supplychain_depth` — A03 Software Supply Chain Failures: **SBOM completeness + signature integrity** (the net-new surface; typosquat / CI-hardening / diff-secrets are already covered by `security/{supplychain,ci_workflow_hardening,diff_secret_gate}`).
- `security_logging` — A09 Security Logging & Alerting Failures: audit coverage, log injection, alert thresholds, hash-chain tamper-evidence.
- `rate_limit` — A06 Insecure Design: throttle, lockout, business-rule abuse, replay/nonce.
- `session` — A07 Authentication Failures / A01 Broken Access Control: session fixation, CSRF token, session-id entropy, timeout.
- `exceptional_conditions` — A10 Mishandling of Exceptional Conditions (new in 2025): fail-open guards, swallowed exceptions, error leakage, resource leaks.
- `ast_sast` — cross-cutting zero-dep AST SAST: CWE-tagged rules; the oracle for `ai/secure_codegen_eval`.

**ai/ — OWASP Top 10 for LLM Applications 2025**
- `excessive_agency` — LLM06 Excessive Agency: tool allowlist, destructive-action confirmation, blast-radius cap.
- `insecure_output_handling` — LLM05 Improper Output Handling: output sinks, XSS, structured-output validation.
- `sensitive_disclosure` — LLM02 Sensitive Information Disclosure: secret / PII / system-prompt-leak detection.
- `unbounded_consumption` — LLM10 Unbounded Consumption: token budget, loop guard, cost ceiling.
- `secure_codegen_eval` — scores generated code on correctness + security (secure-pass@k); repair-loop lift.
- `prompt_ab` — A/B a prompting technique's secure-pass@k delta over the OWASP set.

Deferred (noted, not built): OWASP web A08 (Software/Data Integrity Failures);
LLM04 (Data Poisoning), LLM07 (System Prompt Leakage), LLM09 (Misinformation).

## Running Everything

```bash
make test          # python -m unittest discover -s tests -t . -p "test_*.py"
make selftest      # run --self-test for every harness
make proof         # run --self-test plus proof audit for every harness
make report        # regenerate STATUS.md and STATUS.json
```

**92 harnesses.** Live per-harness self-test and proof status is in `STATUS.md`
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
