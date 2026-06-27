# Harness Map Batch 6

This file maps inventory entries #26-#30 in order: `core/errorpath`, `core/cache`, `core/ratelimit`, `core/webhook`, `core/i18n`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Current proof status is read from `cards/teeth_ratchet.json`: `core/errorpath` = `required`, `core/cache` = `required`, `core/ratelimit` = `required`, `core/webhook` = `required`, `core/i18n` = `required`.

## 26. Error-Path / Negative Coverage Test Harness

- Name: Error-Path / Negative Coverage Test Harness
- Path: `harnesses/core/errorpath_test_harness.py`
- Category: `core`
- Failure class: Targets the #1 AI-generated-code failure mode: happy-path-only code that skips error/null/exception/early-return branches. A `CoverageProbe` records which labelled branches execute across an input battery and flags never-hit error branches (a deliberately-broken `broken_divide` that omits the null guard is caught). An ExceptionPathTester forces each declared exception type and asserts the right type, message, and unchanged state after failure (no partial mutation). A NullHandlingTester injects None into every parameter position; a BoundaryTester fires guard clauses (empty/zero/negative/oversize); a TimeoutTester aborts a slow op cleanly with no partial data; a ResourceCleanupTester verifies try/finally release via acquire/release counters (with a leaking impl flagged).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_leaked`, `LEAK_CORPUS`.
- Planted-bad case: `ignores_balance`, `off_by_one`
- Oracle / proof target: Current proof target: `oracle_leaked`, `LEAK_CORPUS`.
- External testing pattern: error-path / negative coverage fixture and regression testing.
- Current outside reference: Python `unittest` documents exception assertions and test fixtures for exercising expected failure paths. <https://docs.python.org/3/library/unittest.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/errorpath_test_harness.py`; `python harnesses/core/errorpath_test_harness.py --self-test`; `python harnesses/core/errorpath_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_errorpath_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/cache`, `core/ratelimit`, `core/webhook`, `core/i18n`.

## 27. Caching Correctness Test Harness

- Name: Caching Correctness Test Harness
- Path: `harnesses/core/cache_test_harness.py`
- Category: `core`
- Failure class: Tests the classic silent-failure surface: caches that “work” in the demo but serve stale/wrong data under writes and concurrency. A `Cache` (injectable clock, per-entry TTL, max-size LRU) is the correct reference; a `BuggyCache` that skips invalidation proves the harness catches stale-after-write. Covers TTL expiry (just-before vs just-after via clock advance), invalidation-on-write, cache stampede / thundering herd (a `SingleFlightCache` computes a cold key exactly once via per-key lock while a `NaiveCache` races to N loader calls), negative caching with its own TTL, true LRU eviction (least-recently-*used*, recency updated on touch), and namespace key-collision isolation. CacheStats tracks hits/misses/evictions/ratio.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `Cache`, `CACHE_CORPUS`.
- Planted-bad case: `stale_after_write`, `serves_expired`, `no_lru_eviction`
- Oracle / proof target: Current proof target: `Cache`, `CACHE_CORPUS`.
- External testing pattern: caching correctness fixture and regression testing.
- Current outside reference: Python `functools` documents caching helpers such as `lru_cache`, including cache hit/miss behavior. <https://docs.python.org/3/library/functools.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/cache_test_harness.py`; `python harnesses/core/cache_test_harness.py --self-test`; `python harnesses/core/cache_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_cache_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/errorpath`, `core/ratelimit`, `core/webhook`, `core/i18n`.

## 28. Rate Limiting / Throttling Test Harness

- Name: Rate Limiting / Throttling Test Harness
- Path: `harnesses/core/ratelimit_test_harness.py`
- Category: `core`
- Failure class: Tests rate-limiter correctness across four algorithms, all driven by an injectable `FakeClock` (no real sleeps). TokenBucket (burst to capacity, correct refill math, tokens never exceed cap), LeakyBucket (steady drain), FixedWindow (the classic boundary-burst bug — 2× limit across a window edge is detected and reported as the known weakness), and SlidingWindow (proven to prevent that burst). PerKeyTokenBuckets gives independent buckets per API key/IP. A 429 + Retry-After path yields the correct wait, and advancing the clock by it admits the next request. A threaded concurrency stress asserts a locked bucket never over-admits, with a naive unlocked counter illustrating the TOCTOU race.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_simulate`, `TIMELINE_CORPUS`.
- Planted-bad case: `refill_off_by_one`, `uncapped_refill`
- Oracle / proof target: Current proof target: `oracle_simulate`, `TIMELINE_CORPUS`.
- External testing pattern: rate limiting / throttling fixture and regression testing.
- Current outside reference: IETF RFC 6585 defines HTTP 429 Too Many Requests for rate-limiting responses. <https://www.rfc-editor.org/rfc/rfc6585>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/ratelimit_test_harness.py`; `python harnesses/core/ratelimit_test_harness.py --self-test`; `python harnesses/core/ratelimit_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_ratelimit_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/errorpath`, `core/cache`, `core/webhook`, `core/i18n`.

## 29. Webhook Delivery / Verification Test Harness

- Name: Webhook Delivery / Verification Test Harness
- Path: `harnesses/core/webhook_test_harness.py`
- Category: `core`
- Failure class: Tests webhook reliability — a surface that fails silently in production. HMAC-SHA256 signature verification accepts valid signatures and rejects tampered bodies, wrong secrets, and malformed headers, using constant-time `hmac.compare_digest` (a naive `==` compare is flagged as timing-unsafe). A timestamp tolerance / replay window (injectable clock) rejects stale and replayed-but-valid signatures. At-least-once delivery is deduped per event-id (exactly-once side effects), complementary to the Idempotency harness. A sender retries 5xx/timeout with exponential backoff (asserted schedule, max-attempts cap, 2xx stops retries), a flaky receiver is delivered eventually, exhausted events land in a dead-letter queue with reason, and sequence numbers detect out-of-order / gapped delivery.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `oracle_validate`, `SIG_CORPUS`.
- Planted-bad case: `skips_replay_check`, `off_by_one_tolerance`
- Oracle / proof target: Current proof target: `oracle_validate`, `SIG_CORPUS`.
- External testing pattern: webhook delivery / verification fixture and regression testing.
- Current outside reference: Stripe webhook guidance documents signature verification and replay-style event handling patterns. <https://docs.stripe.com/webhooks>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/webhook_test_harness.py`; `python harnesses/core/webhook_test_harness.py --self-test`; `python harnesses/core/webhook_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_webhook_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/errorpath`, `core/cache`, `core/ratelimit`, `core/i18n`.

## 30. i18n / Unicode / Encoding Test Harness

- Name: i18n / Unicode / Encoding Test Harness
- Path: `harnesses/core/i18n_test_harness.py`
- Category: `core`
- Failure class: Tests text-handling correctness that AI code routinely botches (distinct from the format-focused Serialization harness #15). Covers encoding round-trips (utf-8/utf-16/latin-1/ascii), already-corrupted mojibake detection with a clean accented negative, BOM detection/stripping (utf-8-sig, utf-16 LE/BE), surrogate-pair / astral-plane handling (emoji code-point vs UTF-16-unit vs byte counts all differ; lone surrogates flagged), grapheme-vs-code-point counting (ZWJ family emoji: naive `len()`=7 vs grapheme=1), NFC/NFD normalization (byte-unequal but normalize-equal; the un-normalized dedup bug is demonstrated), casefolding (German ß → ss, Turkish dotless-ı trap), byte-safe truncation and East-Asian display width, and RTL/bidi detection including a flagged Trojan-Source bidi-override injection.
- Logic shape: AND: Unicode normalization, mojibake detection, grapheme counting, safe truncation, display width, bidi detection, paired tests, and TEETH swap-check must all hold. NOT: byte/code-point-only shortcuts must not pass as equivalent Unicode handling.
- Good case: `oracle_i18n_audit` matches the frozen `I18N_AUDIT_CORPUS` for NFC/NFD equivalence, mojibake-positive and clean-negative samples, ZWJ graphemes, UTF-8-safe truncation, East-Asian width, and bidi override detection.
- Planted-bad case: `raw_normalization_auditor`, `generated_mojibake_auditor`, `naive_grapheme_auditor`, `byte_slice_truncation_auditor`, and `bidi_blind_auditor`.
- Oracle / proof target: Current proof target: `oracle_i18n_audit`, `I18N_AUDIT_CORPUS`.
- External testing pattern: i18n / unicode / encoding fixture and regression testing.
- Current outside reference: Unicode UAX #15 defines normalization forms, and W3C Character Model guidance covers string matching and normalization concerns. <https://www.unicode.org/reports/tr15/> <https://www.w3.org/TR/charmod-norm/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/i18n_test_harness.py`; `python harnesses/core/i18n_test_harness.py --self-test`; `python harnesses/core/i18n_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_i18n_test_harness tests.core.test_i18n_proof`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, locale-complete collation, or final harness maturity. This dossier maps current source, tests, and ratchet state; it is expected to change.
- Related harnesses: `core/errorpath`, `core/cache`, `core/ratelimit`, `core/webhook`.

## Batch 6 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-06-errorpath-cache-ratelimit-webhook-i18n.md`
- `docs/harness-map/README.md`

Scope note: this batch file originated in a docs-only mapping PR. The current teeth-campaign update changes `core/i18n` source/tests/cards and refreshes this dossier to the new required ratchet state.
