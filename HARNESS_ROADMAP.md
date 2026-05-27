# Harness Roadmap

Forward-looking companion to `HARNESS_INVENTORY.md`. The inventory documents
what exists; this file documents what's next and what's known-missing inside
existing harnesses.

---

## This pass (in progress)

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

## Next-batch candidates

Deferred from this pass; pick from these when next adding harnesses.

### Gap-fill / general-purpose

- **queue/messaging** — at-least-once vs exactly-once, DLQ routing,
  ordering, consumer-group rebalance, redelivery-after-ack-timeout,
  backpressure.
- **tracing/observability** — span hierarchy validity, trace/span-ID
  propagation, sampling consistency, attribute schema, orphan-span
  detection, clock-skew tolerance.
- **gRPC contract** — proto round-trip, deadline propagation, stream
  half-close semantics, status-code coverage.
- **GraphQL contract** — schema-vs-resolver coverage, N+1 detection,
  fragment cycles, max-depth + max-cost enforcement.
- **browser/E2E surrogate** — headless-browser-free DOM-event scripting
  against a mock server, focus management, form-state regressions.

### New verticals

- **payments / checkout** — authorize → capture → refund state machine,
  idempotency-key replay, partial capture + multi-refund accounting, 3DS
  challenge, decline-code taxonomy, currency precision.
- **IoT / telemetry** — out-of-order MQTT-like ingest, duplicate dedupe,
  device-identity rotation, store-and-forward replay.
- **search relevance** — recall@k, precision@k, MRR, NDCG against fixed
  query/judgment sets; tokenizer + analyzer corner cases.

### AI/LLM deeper

- **RAG eval** — retrieval recall, citation faithfulness, answer
  grounding, context-window-overflow degradation.
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
- `core/a11y`, `core/concurrency`, `core/mutation`, `core/numeric`,
  `security/security` — **CLI contract violation:** these five do not
  accept the documented `--self-test` flag. `a11y` and `concurrency` take
  a positional argument (file path / port int) and crash if given
  `--self-test`. `mutation` uses subparsers (`mutate` / `server`).
  `numeric` and `security` only support starting a server.
  Normalizing these to the standard CLI pattern is a hygiene cleanup
  (each ~10-line change, no logic touched).
- `pharmacy/srs` — **known bug:** `sm2_update` overflows to infinity after
  ~1000 consecutive correct grades because `int(round(interval * ease))`
  multiplies two unbounded growing floats with no cap. Surfaces as
  `OverflowError: cannot convert float infinity to integer`. Failing
  tests today: `test_ease_finite_after_1000_correct`,
  `test_all_self_test_scenarios_pass`, `test_scenario_count_at_least_14`.
  Fix: clamp `ease` (SM-2 convention: cap at ~2.5–4.0) **and** clamp
  `interval` (e.g. 10-year cap). Separate from this pass.
- `pharmacy/srs` — also: SM-2 correctness only; no multi-user-leak /
  privacy surface.
- (Add entries here as `make selftest` reports surface them.)

---

## Hygiene backlog

Repo-level cleanup not tied to a single harness:

- Auto-publish `STATUS.md` as a GitHub Pages badge.
- Coverage report by category (probably requires opt-in `coverage` dev-dep
  — won't break the zero-runtime-dep rule).
- Per-harness benchmark in CI for regression detection on `--self-test`
  duration.
- Pre-commit hook for `ruff check` (opt-in).
