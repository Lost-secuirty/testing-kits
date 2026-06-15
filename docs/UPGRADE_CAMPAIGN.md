# Harness Upgrade Campaign (2026 teeth campaign)

Tracker for bringing every in-scope harness to the GOLD bar: it **works**, **has
teeth** (catches a real planted bug), **fails loud** (nonzero exit + clear signal),
and **reports its findings** (structured). Gate + contract are documented in
[PROOF_TEST_STANDARD.md](PROOF_TEST_STANDARD.md); the machinery lives in
`harnesses/_teeth.py`, `tools/proof_audit.py`, `tools/teeth_check.py`.

## Scope & rules

- **In scope: 69 harnesses** — core (52), security (10), ai (7).
- **Out of scope: pharmacy (8)** — kept on the legacy soft gate; the operator will
  do that category later.
- Batch size: **≤10 when upgrading**, 6 for brand-new harnesses.
- **GOLD harnesses are upgraded, not rewritten**; non-GOLD may be rewritten freely,
  with a one-line rationale in [LEARNINGS.md](LEARNINGS.md).
- Order: **weakest/hardest-first** (stubs + BRONZE → SILVER → GOLD-enrich).
- Per batch: research sweep → fixtures → oracle → planted mutant(s) → `prove`+`TEETH`
  → `--self-test`/`--json` `Report` → pair unittest → (security/ai: proof test) →
  flip `pending → required` → draft PR (operator merges).

## Status snapshot (Batch 2, 2026-06-15)

| Scope | Count | Meaning |
|---|---:|---|
| required (teeth verified) | 29 | proven by the swap-check; gate blocks on these |
| pending | 40 | in scope, no `TEETH` yet — counted, non-blocking |
| legacy (pharmacy) | 8 | older soft gate, out of campaign |

**required (29):** Batch 0 (9) core/{check_digit_identifier,feature_flag,graphql,
grpc_contract,idempotency,queue,tracing}, security/{ci_workflow_hardening,diff_secret_gate}
· Batch 1 (10) core/{api,cache,cli,config,contract,null_propagation,pagination,serialization,
statemachine}, security/authz · Batch 2 (10) core/{db,scraper,fuzz,numeric,concurrency,
error_path_leak,schema_evolution}, security/{supplychain,upload}, ai/agent_memory_context.

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
- **Batch 3+ — drain remaining pending → SILVER, then GOLD enrich.** Likely
  quick-win "near-GOLD" candidates (already have an oracle/twin or planted-bad
  fixtures — mostly a `TEETH` wiring + real `--self-test`): core/statistical_rng_oracle,
  core/payments, core/canvas_scene_state, core/game_loop_simulation, core/iot_telemetry,
  core/browser_e2e, core/lexical_date_canonicalization, security/cwe_kev_regression,
  ai/agent_eval, ai/drift_detection, ai/prompt_injection.

## Per-tier research to apply (verified 2026-06-14; act at the batch)
- **security/jwt** — CVE-2026-48526 (public JWK string accepted as HMAC secret →
  forged HS256, alg confusion), CVE-2026-48523 (alg allow-list bypass). Mutants.
- **security/ci_workflow_hardening** (already GOLD — enrich) — pwn_request
  CVE-2026-45132 (CVSS 10), actions-cool tag-redirect, Shai-Hulud/Miasma OIDC token
  theft. New rules + mutants.
- **ai/\*** — arXiv:2606.11686 "Layer-Isolated Evaluation" (validates the no-LLM,
  per-slice, CI-gated approach + an adoptable layer taxonomy); Microsoft ASSERT
  (spec→eval scenario generator — freeze outputs into fixtures).

## Known issues found in Batch 0 (fix in the relevant tier batch)

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

**core (52):** R api, cache, check_digit_identifier, cli, concurrency, config, contract,
db, error_path_leak, feature_flag, fuzz, graphql, grpc_contract, idempotency,
null_propagation, numeric, pagination, queue, schema_evolution, scraper, serialization,
statemachine, tracing · P a11y, browser_e2e, canvas_scene_state, cardinality, chaos,
circuitbreaker, clock_skew, complexity, datetime, dormant_code, errorpath,
game_loop_simulation, hermeticity, i18n, iot_telemetry, lexical_date_canonicalization,
logging, memory, mutation, network, payments, pipeline, property, ratelimit,
regression_snapshot, search_relevance, statistical_rng_oracle, stress, webhook

**security (10):** R authz, ci_workflow_hardening, diff_secret_gate, supplychain, upload
· P appsec, cwe_kev_regression, jwt, pii_redaction, security

**ai (7):** R agent_memory_context · P agent_eval, agentic, drift_detection, llm_eval,
prompt_injection, rag_eval

> `core/stress` also still uses the non-standard `stress_harness.py` filename
> (vs `*_test_harness.py`); rename to `stress_test_harness.py` during its batch.
