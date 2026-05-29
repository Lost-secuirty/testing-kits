# Harness Roadmap

Forward-looking companion to `HARNESS_INVENTORY.md`. The inventory documents
what exists; this file documents what's next and what's known-missing inside
existing harnesses.

---

## Batch 4 — shipped 2026-05-27

10 new harnesses, research-grounded against 2026 sources (CWE Top 25 2025,
OWASP LLM Top 10 2025, ChaosAPI/PACMPL 2025, AI-coded-bug surveys, the
Statsig postmortem of the Google June 2025 outage).

| # | Path | Direction | Source signal |
|---|---|---|---|
| 1 | `harnesses/core/null_propagation_test_harness.py` | Gap-fill | AI-coded bug class #1 (arXiv 2512.05239, 2411.01414) |
| 2 | `harnesses/core/error_path_leak_test_harness.py` | Gap-fill | 2025 CWE Top 25 added "Allocation w/o Throttling" |
| 3 | `harnesses/core/feature_flag_test_harness.py` | Gap-fill | Google June 2025 outage root cause (Statsig) |
| 4 | `harnesses/core/clock_skew_test_harness.py` | Gap-fill | Distributed-time bugs (Bhayani, Scalar Dynamic 2025) |
| 5 | `harnesses/core/schema_evolution_test_harness.py` | Gap-fill | 60%+ of pipelines hit silent schema drift (Matia 2025) |
| 6 | `harnesses/core/dormant_code_test_harness.py` | Gap-fill | Untriggered branches first-hit crash (Google June 2025) |
| 7 | `harnesses/core/cardinality_test_harness.py` | Gap-fill | Metric/cache cardinality explosion (Sawmills 2025) |
| 8 | `harnesses/core/hermeticity_test_harness.py` | Gap-fill | Top flaky-test class (ACM PACMPL ChaosAPI 2025) |
| 9 | `harnesses/pharmacy/drug_interaction_test_harness.py` | Pharmacy | Continues pharmacy batch |
| 10 | `harnesses/ai/prompt_injection_test_harness.py` | AI/LLM | OWASP LLM Top 10 2025 (LLM01 + new entries) |

See the plan file for sketches and acceptance criteria.

---

## Batch 5 — shipped 2026-05-29

6 harnesses (max-6 batch), from the Next-batch candidates below.

| # | Path | Direction | Self-test |
|---|---|---|---|
| 54 | `harnesses/core/tracing_test_harness.py` | Gap-fill (observability) | 22 scenarios |
| 55 | `harnesses/core/queue_test_harness.py` | Gap-fill (messaging) | 20 scenarios |
| 56 | `harnesses/core/search_relevance_test_harness.py` | New vertical (IR) | 22 scenarios |
| 57 | `harnesses/ai/rag_eval_test_harness.py` | AI/LLM deeper | 19 scenarios |
| 58 | `harnesses/core/graphql_test_harness.py` | Gap-fill (contract) | 21 scenarios |
| 59 | `harnesses/core/payments_test_harness.py` | New vertical (commerce) | 27 scenarios |

All six pass `--self-test` (exit 0) and their paired suites (135 unit tests
total). Five in `core`, one in `ai`; ports 19300/19310/19320 reserved (oracles
run in-process). Self-contained per repo convention (no cross-harness imports;
`Money`/FSM re-derived locally).

---

## Next-batch candidates

Deferred; pick from these when next adding harnesses.

### Gap-fill / general-purpose

- **gRPC contract** — proto round-trip, deadline propagation, stream
  half-close semantics, status-code coverage.
- **browser/E2E surrogate** — headless-browser-free DOM-event scripting
  against a mock server, focus management, form-state regressions.

### New verticals

- **IoT / telemetry** — out-of-order MQTT-like ingest, duplicate dedupe,
  device-identity rotation, store-and-forward replay.

### AI/LLM deeper

- **drift detection** — embedding-space drift between model versions,
  prompt-template drift, output-distribution drift.
- **multi-turn agent eval** — task-completion across N turns, tool-use
  recovery from intermediate errors, state-leak between turns.

---

## Per-harness internal gaps

Seeded from a `make selftest` sweep + the bug-research findings. Each item
is a known thinness that could be filled without a whole new harness.

> Convention: when a fix lands, delete the bullet here. When a new gap is
> discovered, add it.

- `core/cache` — no cardinality-explosion coverage (now subsumed by
  `core/cardinality` in this pass; cross-link only).
- `core/memory` — soak-focused; does **not** exercise acquire/release
  error-path pairs (subsumed by `core/error_path_leak` in this pass).
- `core/datetime` — TZ + DST coverage strong; clock-skew + NTP-jump weak
  (subsumed by `core/clock_skew` in this pass).
- `ai/llm_eval` — quality eval only; no injection corpus (subsumed by
  `ai/prompt_injection` in this pass).
- `ai/agentic` — tool-call safety; no system-prompt-leakage probes
  (subsumed by `ai/prompt_injection`).
- `pharmacy/srs` — SM-2 correctness only; no multi-user-leak / privacy surface.
- `core/numeric` — `PrecisionTester.float_inexact_sum()` sums to *exactly* 1.0 on
  CPython, so `test_float_inexact_sum_not_exactly_one` and the precision-endpoint
  test assert inexactness and fail (2 unit tests). Pre-existing; fix by choosing
  genuinely-inexact operands (e.g. `0.1 + 0.2`) or relaxing the assertion. No
  `--self-test` impact.
- (Add entries here as `make selftest` reports surface them.)

### Resolved 2026-05-29 — STATUS.md now 59/59 green (self-test 138s → 50s)
- **CLI contract** for `a11y`, `concurrency`, `mutation`, `numeric`, `security` —
  all now accept `--self-test` with real in-process scenarios (commit f62901a);
  server/file behaviors preserved behind `--serve` / positional args.
- **`pharmacy/srs` overflow** — `sm2_update` interval clamped at `INTERVAL_CAP`
  (36500 d) so repeated-correct growth can't reach a float `OverflowError` (a23db40).
- **`core/hermeticity` `depends_on_home`** — probe now mocks (and restores) both
  `USERPROFILE` and `HOME`, so `Path.home()` dependence is detected on Windows (a23db40).
- **Windows portability** — `core/memory` guards `import resource` and adds ctypes
  `GetProcessMemoryInfo`/`GetProcessHandleCount` (RSS/fd > 0 on Windows);
  `ai/prompt_injection` + `pharmacy/partial_fill` reconfigure stdout to UTF-8 at
  import; `core/numeric` `--self-test` returns immediately instead of timing out
  (6a55f16, 13543d8).

---

## Hygiene backlog

Repo-level cleanup not tied to a single harness:

- Auto-publish `STATUS.md` as a GitHub Pages badge.
- Coverage report by category (probably requires opt-in `coverage` dev-dep
  — won't break the zero-runtime-dep rule).
- Per-harness benchmark in CI for regression detection on `--self-test`
  duration.
- Pre-commit hook for `ruff check` (opt-in).
