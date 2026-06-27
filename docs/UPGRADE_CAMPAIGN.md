# Harness Upgrade Campaign (2026 teeth campaign)

Tracker for bringing every in-scope harness to the GOLD bar: it **works**, **has
teeth** (catches a real planted bug), **fails loud** (nonzero exit + clear signal),
and **reports its findings** (structured). Gate + contract are documented in
[PROOF_TEST_STANDARD.md](PROOF_TEST_STANDARD.md); the machinery lives in
`harnesses/_teeth.py`, `tools/proof_audit.py`, `tools/teeth_check.py`.

This file is a campaign history and planning record. For the current documented
count snapshot, check `docs/GOLDEN_STATS.md`; for fresh proof, run the relevant
proof commands.

## Scope & rules

- **Original campaign scope: 69 harnesses** — core (52), security (10), ai (7).
- **Current documented non-pharmacy TEETH scope after Batch 11: 92 required harnesses** — the original 69 plus 15 OWASP web/LLM harnesses (Batch 10) plus 8 Exploratory Proof Layer + deferred-OWASP harnesses (Batch 11).
- **Out of scope: pharmacy (8)** — kept on the legacy soft gate; the operator will
  do that category later.
- Batch size: **≤10 when upgrading**, 6 for brand-new harnesses.
- **GOLD harnesses are upgraded, not rewritten**; non-GOLD may be rewritten freely,
  with a one-line rationale in [LEARNINGS.md](LEARNINGS.md).
- Order: **weakest/hardest-first** (stubs + BRONZE → SILVER → GOLD-enrich).
- Per batch: research sweep → fixtures → oracle → planted mutant(s) → `prove`+`TEETH`
  → `--self-test`/`--json` `Report` → pair unittest → (security/ai: proof test) →
  flip `pending → required` → draft PR (operator merges).

## Status snapshot

| Snapshot | Scope | Count | Meaning |
|---|---|---:|---|
| Batch 9, 2026-06-18 | required (teeth verified) | 69 | Original non-pharmacy campaign complete; swap-check covers required harnesses. |
| Batch 9, 2026-06-18 | pending | 0 | Original in-scope campaign had no pending harnesses left. |
| Batch 9, 2026-06-18 | legacy (pharmacy) | 8 | Older soft gate, out of campaign. |
| Batch 10, 2026-06-21 | required (teeth verified) | 84 | Batch 10 added 15 OWASP web/LLM harnesses as required TEETH harnesses. |
| Batch 10, 2026-06-21 | pending | 0 | No pending harnesses in the documented Batch 10 snapshot. |
| Batch 10, 2026-06-21 | legacy (pharmacy) | 8 | Pharmacy-domain harnesses remain separately labelled legacy. |
| Batch 11, 2026-06-27 | required (teeth verified) | 92 | Batch 11 added 8 harnesses (4 core Exploratory Proof Layer + 4 deferred-OWASP). Total inventory 100 (core 56, security 20, ai 16, pharmacy 8). |
| Batch 11, 2026-06-27 | pending | 0 | No pending harnesses in the documented Batch 11 snapshot. |
| Batch 11, 2026-06-27 | legacy (pharmacy) | 8 | Pharmacy-domain harnesses remain separately labelled legacy. |

Do not treat this table as fresh proof output. Re-run `make proof` before release claims.

**required after Batch 9 (69):** Batch 0 (9) core/{check_digit_identifier,feature_flag,graphql,
grpc_contract,idempotency,queue,tracing}, security/{ci_workflow_hardening,diff_secret_gate}
· Batch 1 (10) core/{api,cache,cli,config,contract,null_propagation,pagination,serialization,
statemachine}, security/authz · Batch 2 (10) core/{db,scraper,fuzz,numeric,concurrency,
error_path_leak,schema_evolution}, security/{supplychain,upload}, ai/agent_memory_context ·
Batch 3 (10) core/{statistical_rng_oracle,payments,canvas_scene_state,game_loop_simulation,
iot_telemetry,browser_e2e,lexical_date_canonicalization}, security/cwe_kev_regression,
ai/{agent_eval,drift_detection} · Batch 4 (6) core/{chaos,datetime,errorpath,pipeline,
property}, ai/llm_eval · Batch 5 (6) core/{logging,memory,ratelimit,regression_snapshot,
webhook}, security/appsec · Batch 6 (3) core/{mutation,network}, security/security ·
Batch 7 (5) core/{stress,i18n,a11y,clock_skew}, ai/agentic · Batch 8 (5)
core/{cardinality,dormant_code,hermeticity,search_relevance}, ai/prompt_injection.
· Batch 9 (5) core/{circuitbreaker,complexity}, security/{jwt,pii_redaction}, ai/rag_eval.

**Batch 10 additions (15):** security/{crypto,misconfig,advanced_injection,supplychain_depth,
security_logging,rate_limit,session,exceptional_conditions,ast_sast} and
ai/{excessive_agency,insecure_output_handling,sensitive_disclosure,unbounded_consumption,
secure_codegen_eval,prompt_ab}.

**Batch 11 additions (8):** the Exploratory Proof Layer + deferred-OWASP closeout —
core/{combinatorial_coverage,counterexample_replay,stateful_sequence_budget,boundary_corpus_expander}
and security/data_integrity (OWASP A08:2025), ai/data_poisoning (LLM04:2025),
ai/system_prompt_leakage (LLM07:2025), ai/misinformation (LLM09:2025).

## Batch roadmap (provisional; exact membership ranked at each batch start)

- **Batch 0 — Foundation (this PR):** teeth machinery, hardened gate, advisory
  mutmut lane, GOLD template, 9 GOLD anchors, docs. No non-GOLD harness rewrites.
- **Batch 1 — DONE (2026-06-15):** real TEETH wired into 10 BRONZE/near-GOLD harnesses,
  all flipped pending → required: security/authz, core/config, core/contract (these 3
  were substantial BRONZE, NOT "near-empty stubs" as first assumed), core/api, core/cache,
  core/cli, core/serialization, core/pagination, core/statemachine, core/null_propagation.
  Pattern: frozen literal corpus + reused-correct-logic oracle + faithful planted
  mutant(s) + Report `--self-test` + paired `TestTeeth`; adversarially verified non-circular.
- **Batch 2 — DONE (2026-06-15):** real TEETH wired into 10 heavy-rewrite harnesses, all
  flipped pending → required: core/db, core/scraper, core/fuzz, core/numeric,
  core/concurrency, core/error_path_leak, core/schema_evolution, security/supplychain,
  security/upload, ai/agent_memory_context. Notable: numeric mutants use an explicit `+=`
  loop (sum() Neumaier-compensates on 3.12+); concurrency models the race via a
  deterministic interleaving (no real threads in prove); the ai harness judges against
  frozen retrieved-id literals (NOT a model/embedding — the AI-eval circularity trap).
- **Batch 3 — DONE (2026-06-15):** real TEETH wired into 10 quick-win near-GOLD harnesses,
  all flipped pending → required: core/{statistical_rng_oracle,payments,canvas_scene_state,
  game_loop_simulation,iot_telemetry,browser_e2e,lexical_date_canonicalization},
  security/cwe_kev_regression, ai/{agent_eval,drift_detection}. Kinds span `oracle_swap`,
  `auditor` (cwe_kev, agent_eval), and `statistical` (statistical_rng_oracle, drift_detection).
  **ai/prompt_injection deferred** to a later batch so it can absorb the per-tier
  layer-isolated-eval research (arXiv:2606.11686) rather than a thin wire now.
- **Batch 4 — DONE (2026-06-16):** real TEETH wired into the 6 weakest pending harnesses,
  all flipped pending → required: core/{chaos,datetime,errorpath,pipeline,property}, ai/llm_eval.
  All `oracle_swap`; each `prove` judges a frozen-literal corpus (non-circular, swap-verified;
  confirmed by reading every `prove` body + a flip-a-literal test per harness). `network` was
  deferred — its headline socket/DNS/timeout behavior is non-deterministic, so it earns its own
  extraction batch (the `prompt_injection` rationale). Built via a bounded 6-agent Workflow;
  gate 39 → 45 required / 24 pending / 8 legacy / 0 failing.
- **Batch 5 — DONE (2026-06-16):** real TEETH wired into 6 more pending harnesses, all
  flipped pending → required: core/{logging,memory,ratelimit,regression_snapshot,webhook},
  security/appsec. Mostly `oracle_swap`; security/appsec is an `auditor` (SSRF allow/deny over
  a frozen target corpus). webhook + ratelimit drive a deterministic `FakeClock` (HMAC replay
  window, token-bucket refill) so no wall-clock enters `prove`. Each `prove` judges a
  frozen-literal corpus (non-circular, swap-verified; confirmed by reading every `prove` body
  + a flip-a-literal test per harness). Built via a bounded 6-agent Workflow; gate 45 → 51
  required / 18 pending / 8 legacy / 0 failing. Known future-hardening item: memory's
  `threshold_boundary` mutant is single-case (inherent to a `>=`/`>` slope bug).
- **Batch 6 — DONE (2026-06-18):** real TEETH wired into 3 more pending harnesses, all
  flipped pending → required: core/{mutation,network}, security/security. Gate 51 → 54
  required / 15 pending / 8 legacy / 0 failing.
- **Batch 7 — DONE (2026-06-18):** real TEETH wired into 5 more pending harnesses, all
  flipped pending → required: core/{stress,i18n,a11y,clock_skew}, ai/agentic. Research anchors:
  k6 open-vs-closed load models for stress, Unicode/W3C normalization for i18n, WCAG 2.2 for
  a11y, agent process/trajectory evaluation papers for agentic tool-use checks, and RFC 5905
  for clock-skew/NTP context. Gate 54 → 59 required / 10 pending / 8 legacy / 0 failing.
- **Batch 8 — DONE (2026-06-18):** real TEETH wired into 5 more pending harnesses, all
  flipped pending → required: core/{cardinality,dormant_code,hermeticity,search_relevance},
  ai/prompt_injection. Research anchors: Coverage.py branch coverage for dormant-path
  discovery, OpenTelemetry cardinality guidance, Bazel hermetic-test expectations, OWASP
  LLM01 prompt injection, and OpenSearch relevance evaluation. Gate 59 → 64 required /
  5 pending / 8 legacy / 0 failing.
- **Batch 9 — DONE (2026-06-18):** real TEETH wired into the last 5 pending harnesses,
  all flipped pending → required: core/{circuitbreaker,complexity}, security/{jwt,
  pii_redaction}, ai/rag_eval. Research anchors: Azure circuit-breaker pattern,
  RFC 8725/RFC 7519 for JWT verification, NIST SP 800-122 for PII protection,
  Microsoft RAG evaluators / TREC relevance-evaluation practice, and Radon plus
  Sonar Cognitive Complexity for maintainability metrics. Gate 64 → 69 required /
  0 pending / 8 legacy / 0 failing.
- **Batch 10 — DONE (2026-06-21):** 15 OWASP Top 10:2025 web + LLM harnesses added as required
  TEETH harnesses. Documented snapshot: 84 required / 0 pending / 8 legacy / 0 failing.
- **Batch 11 — DONE (2026-06-27):** 8 harnesses added as required TEETH harnesses — 4 Exploratory
  Proof Layer (combinatorial_coverage, counterexample_replay, stateful_sequence_budget,
  boundary_corpus_expander) + 4 deferred-OWASP (data_integrity A08, data_poisoning LLM04,
  system_prompt_leakage LLM07, misinformation LLM09). Documented snapshot: 92 required /
  0 pending / 8 legacy / 0 failing; inventory 100.
- **Post-Batch 11:** docs consistency, mapping freshness, proof-claim hygiene, and legacy/pharmacy
  boundary language take priority over new harness inventory.
- **Historical candidate backlog (not approved by this file):**
  1. **gherkin_spec_determinism** — enforce constrained-Gherkin rules (declarative-not-
     imperative, single `Feature`, strict Given/When/Then order, no XPath/DB-schema leak);
     mutant = an imperative / multi-behavior / mechanic-leaking scenario that slips through.
  2. **spec_gaming_guard** (hidden-test-split) — prove an impl GENERALIZES rather than
     overfitting the visible tests; mutant = an impl that passes the visible split but fails
     the withheld one. Most on-theme — a direct anti-vacuous-green oracle.
  3. **context_compaction** (Sawtooth) — prove a compaction drops only irrelevant items and
     keeps every required one; mutant = a compaction that evicts a required item.
  4. **doc_freshness** — deterministic code↔doc divergence/staleness score; mutant = a stale
     doc the scorer fails to flag.
  5. **prompt_cache_prefix_stability** — dynamic content (timestamp / live query) in the
     cached prefix must force a cache miss; mutant = a prefix layout that silently
     invalidates yet still reports a cache hit.
  6. **automation_analytics_metrics** — compute Defect-Escape / Stability / Pass-Rate over a
     frozen test-run corpus; mutant = a calc that counts a flaky test as a pass.
  Provenance + the doc fact-check corrections live in memory `project_gemini_doc_idea_backlog`.

## Per-tier research to apply (verified 2026-06-14; act at the batch)
- **security/jwt** — applied in Batch 9: alg=none, alg allow-list, signature, time-claim,
  and required-claim mutants. Future enrichment can add public-JWK-as-HMAC-secret fixtures
  if the harness grows beyond HS256-only stdlib verification.
- **security/ci_workflow_hardening** (already GOLD — enrich) — pwn_request
  CVE-2026-45132 (CVSS 10), actions-cool tag-redirect, Shai-Hulud/Miasma OIDC token
  theft. New rules + mutants.
- **ai/** — arXiv:2606.11686 "Layer-Isolated Evaluation" (validates the no-LLM,
  per-slice, CI-gated approach + an adoptable layer taxonomy); Microsoft ASSERT
  (spec→eval scenario generator — freeze outputs into fixtures).

## Known issues found in Batch 0 (fix in the relevant tier batch)

These are historical Batch 0 findings. Check live proof output before treating any item here as current.

- **core/datetime** — looked GOLD (grep hit on a "buggy"/"naive" string) but is a
  class library: no oracle predicate, no buggy twin, no frozen corpus, and its
  `--self-test` is a no-op (no argparse). Needs a full upgrade, not a TEETH add.
- **core/idempotency** — anchored (teeth verified), but its `--self-test` is a no-op
  (no argparse/main); add a real `Report`-based `--self-test` in its batch.
- **General**: many "pending" harnesses pass `--self-test` only because they have no
  argparse and exit 0 trivially. The TEETH swap-check — not `--self-test` exit code —
  is the real signal. Each upgrade must add a genuine `Report`-based self-test.

## Full in-scope status
Legend: `R` required · `P` pending · (pharmacy = legacy, omitted).

**core (52):** R a11y, api, browser_e2e, cache, canvas_scene_state, cardinality, chaos,
check_digit_identifier, circuitbreaker, cli, clock_skew, complexity, concurrency, config,
contract, datetime, db, dormant_code, error_path_leak, errorpath, feature_flag, fuzz,
game_loop_simulation, graphql, grpc_contract, hermeticity, idempotency, i18n, iot_telemetry,
lexical_date_canonicalization, logging, memory, mutation, network, null_propagation,
numeric, pagination, payments, pipeline, property, queue, ratelimit, regression_snapshot,
schema_evolution, scraper, search_relevance, serialization, statemachine,
statistical_rng_oracle, stress, tracing, webhook

**security (19):** R advanced_injection, appsec, ast_sast, authz, ci_workflow_hardening,
crypto, cwe_kev_regression, diff_secret_gate, exceptional_conditions, jwt, misconfig,
pii_redaction, rate_limit, security, security_logging, session, supplychain,
supplychain_depth, upload

**ai (13):** R agent_eval, agent_memory_context, agentic, drift_detection, excessive_agency,
insecure_output_handling, llm_eval, prompt_ab, prompt_injection, rag_eval, secure_codegen_eval,
sensitive_disclosure, unbounded_consumption

> `core/stress` was renamed `stress_harness.py` -> `stress_test_harness.py` on 2026-06-18,
> closing the last naming exception (it had been promoted under the old name in Batch 7 to
> keep that proof diff scoped). The standard name also makes it discoverable to the vacuity gate.
