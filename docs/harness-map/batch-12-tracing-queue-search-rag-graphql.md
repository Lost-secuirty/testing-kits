# Harness Map Batch 12

This file maps inventory entries #54-#58 in order: `core/tracing`, `core/queue`, `core/search_relevance`, `ai/rag_eval`, `core/graphql`.

This is current-state documentation, not command authority. It maps the source, tests, and ratchet data as they exist for this batch. Older, pending, and legacy harnesses are documented as-is and are expected to keep changing.

Operating rules remain in `AGENTS.md`, `CLAUDE.md`, and `SECURITY.md`.

Proof status is read from `cards/teeth_ratchet.json` at the time this batch is cut: `core/tracing` = `required`, `core/queue` = `required`, `core/search_relevance` = `pending`, `ai/rag_eval` = `pending`, `core/graphql` = `required`.

## 54. Distributed Tracing Test Harness

- Name: Distributed Tracing Test Harness
- Path: `harnesses/core/tracing_test_harness.py`
- Category: `core`
- Failure class: Validates a span set against an oracle and proves it catches a battery of broken traces — the failure modes LLM-written OpenTelemetry glue produces. Strict W3C `traceparent` parse/format (2-32-16-2 lower hex; all-zero trace/span IDs and version `ff` rejected). `validate_trace` checks single-root, no orphan (unresolved parent) or cross-trace parents, no parent-chain cycles, non-negative span durations, head-sampling consistency (a sampled child under an unsampled parent is flagged), required-attribute schema, and clock-skew tolerance (child start before parent beyond a bound). Seven `BUGGY_TRACES` fixtures each flip exactly their target counter; a `Propagator` round-trips context while a `BuggyPropagator` drops it. 22 self-test scenarios.
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `Propagator`, `SCENARIOS`, `_TEETH_CORPUS`.
- Planted-bad case: `propagator_drops_context`
- Oracle / proof target: Current proof target: `Propagator`, `SCENARIOS`, `_TEETH_CORPUS`.
- External testing pattern: distributed tracing fixture and regression testing.
- Usage note: Use this as a telemetry fixture for trace/span linkage, parent-child propagation, sampling, and missing-context regressions.
- Current outside reference: OpenTelemetry traces document spans, traces, context propagation, and distributed telemetry concepts. <https://opentelemetry.io/docs/concepts/signals/traces/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/tracing_test_harness.py`; `python harnesses/core/tracing_test_harness.py --self-test`; `python harnesses/core/tracing_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_tracing_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/queue`, `core/search_relevance`, `core/graphql`.

## 55. Message-Queue Delivery Test Harness

- Name: Message-Queue Delivery Test Harness
- Path: `harnesses/core/queue_test_harness.py`
- Category: `core`
- Failure class: Tests broker delivery semantics with an injectable clock. `InMemoryBroker` (oracle) covers at-least-once redelivery (nack and ack-timeout), exactly-once dedup that survives redelivery, DLQ routing after max deliveries, per-key FIFO via head-of-key delivery, consumer-group rebalance (no loss / no double-delivery of acked / order preserved), ack-timeout heartbeat extension, and backpressure (in-flight cap + publish-depth reject). Four buggy brokers are each proven caught: `NaiveBroker` (acks on poll → crash loses the message), `LossyExactlyOnce` (no dedup → double-process), `OrderBreakingRebalance` (delivers non-head-of-key), `NoDlqBroker` (never routes poison → loops forever). 20 self-test scenarios. Distinct from `idempotency` (the dedup primitive) and `concurrency` (race detection).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `InMemoryBroker`, `SCENARIOS`, `_CONTRACT_CASES`.
- Planted-bad case: `acks_on_poll_loses_on_crash`, `exactly_once_never_dedups`, `no_per_key_serialization`, `never_routes_to_dlq`
- Oracle / proof target: Current proof target: `InMemoryBroker`, `SCENARIOS`, `_CONTRACT_CASES`.
- External testing pattern: message-queue delivery fixture and regression testing.
- Usage note: Use this as a message-delivery fixture for visibility timeout, retries, dedupe, ordering, dead-letter, and at-least-once behavior.
- Current outside reference: Amazon SQS documentation describes message visibility timeout and at-least-once delivery behavior. <https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/welcome.html>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/queue_test_harness.py`; `python harnesses/core/queue_test_harness.py --self-test`; `python harnesses/core/queue_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_queue_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/tracing`, `core/search_relevance`, `core/graphql`.

## 56. Search-Relevance Test Harness

- Name: Search-Relevance Test Harness
- Path: `harnesses/core/search_relevance_test_harness.py`
- Category: `core`
- Failure class: Classic IR ranking metrics over fixed graded judgment sets plus an analyzer corner-case oracle. Computes recall@k, precision@k, MRR, and NDCG (graded gain `2^grade-1`). A stdlib analyzer applies NFKC, casefold, accent-fold, naive plural-stem, stop-word drop, and CJK no-space segmentation, checked against an 8-case oracle. A lexical retriever (distinct-overlap, stable tie-break) over an engineered 20-doc / 6-query corpus lets the oracle meet recall ≥ 0.80 / MRR ≥ 0.70 / NDCG ≥ 0.80; a `reversed_search` ranker falls below the NDCG floor and a `no_fold_analyze` analyzer fails the fold cases. 22 self-test scenarios. Distinct from `llm_eval`/`rag_eval` (LLM answer quality) and `pagination` (cursor consistency).
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: search-relevance fixture and regression testing.
- Usage note: Use this as a ranking regression fixture for query scoring, tie-breaking, normalization, and relevance drift in small controlled corpora.
- Current outside reference: OpenSearch relevance documentation describes scoring, queries, and ranking behavior for search results. <https://opensearch.org/docs/latest/search-plugins/searching-data/index/>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/search_relevance_test_harness.py`; `python harnesses/core/search_relevance_test_harness.py --self-test`; `python harnesses/core/search_relevance_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_search_relevance_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/tracing`, `core/queue`, `core/graphql`.

## 57. RAG Eval Test Harness

- Name: RAG Eval Test Harness
- Path: `harnesses/ai/rag_eval_test_harness.py`
- Category: `ai`
- Failure class: Scores retrieval-augmented answers on four axes RAG fails silently on: retrieval recall@k, citation faithfulness (a citation counts only if it was retrieved AND its passage supports a claim), answer grounding (claims present in the post-overflow context), and context-window overflow (greedy-pack drop of tail passages). A deterministic lexical retriever over an engineered 20-passage / 6-case corpus lets the oracle meet recall ≥ 0.80 / faithfulness ≥ 0.90 / grounding ≥ 0.80. A keyword-only (AND) retriever drops below the recall floor, a truncating retriever degrades grounding, and a citation fabricator drops below the faithfulness floor. 19 self-test scenarios. Distinct from `ai/llm_eval` (answer graders, no retrieval) and `ai/prompt_injection` (safety corpus).
- Logic shape: AND: the current harness, paired tests, and inventory entry must describe the same behavior. NOT: pending status must not be described as TEETH-required proof.
- Good case: The current pending harness exercises the coverage summarized above; this entry maps that evidence as-is without claiming required TEETH proof.
- Planted-bad case: none in required TEETH as of this batch; map the current pending evidence as-is.
- Oracle / proof target: Current proof target: self-test and paired-test evidence visible in the current source, not required TEETH proof.
- External testing pattern: AI-feature evaluation and safety-regression fixture mapping.
- Usage note: Use this as a retrieval-augmented generation fixture for retrieval quality, groundedness, citation coverage, and answer refusal behavior without depending on a live model.
- Current outside reference: OpenAI retrieval guidance describes retrieval-augmented workflows that search external knowledge before generation. <https://platform.openai.com/docs/guides/retrieval>
- Proof status: `pending` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/ai/rag_eval_test_harness.py`; `python harnesses/ai/rag_eval_test_harness.py --self-test`; `python harnesses/ai/rag_eval_test_harness.py --list-scenarios`; `python -m unittest tests.ai.test_rag_eval_test_harness`; `make test-ai`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change. Pending status means no required TEETH proof should be claimed.
- Related harnesses: `core/tracing`, `core/queue`, `core/search_relevance`, `core/graphql`.

## 58. GraphQL Contract Test Harness

- Name: GraphQL Contract Test Harness
- Path: `harnesses/core/graphql_test_harness.py`
- Category: `core`
- Failure class: Parses queries into an AST (selection sets, aliases, fragment spreads, arg-skipping) and runs analyzers: schema-vs-resolver coverage (missing/orphan), query depth, query cost (list fields multiply, nesting compounds, aliases counted), N+1 list-resolver detection (with a dataloader-batched exclusion), fragment-cycle detection (direct and indirect), and unknown field/fragment validation. `enforce_limits` rejects every abusive query (deeply nested, cost-bomb, wide-alias amplification) while a `LeakyResolverSet` (missing + orphan) and a naive no-limit executor are caught. 21 self-test scenarios. Distinct from `core/contract` (arbitrary-callable pre/post/invariants) and `core/api` (REST CRUD).
- Logic shape: AND: source fixture behavior, paired tests, ratchet entry, and TEETH swap-check must all hold. XNOR: `prove()` should agree with the frozen expected corpus for the current source. NOT: a planted mutant must not pass as if it were the oracle.
- Good case: The current oracle path is expected to remain clean against `enforce_limits`, `SCENARIOS`, `_TEETH_CASES`.
- Planted-bad case: `no_limit_executor`
- Oracle / proof target: Current proof target: `enforce_limits`, `SCENARIOS`, `_TEETH_CASES`.
- External testing pattern: graphql contract fixture and regression testing.
- Usage note: Use this as a contract fixture for schema validation, resolver behavior, query shape, pagination, and error formatting.
- Current outside reference: GraphQL documentation describes schema, queries, validation, and resolver-backed API contracts. <https://graphql.org/learn/>
- Proof status: `required` as of current `cards/teeth_ratchet.json`; subject to change as source, tests, or ratchet state changes.
- Commands: `python tools/teeth_check.py harnesses/core/graphql_test_harness.py`; `python harnesses/core/graphql_test_harness.py --self-test`; `python harnesses/core/graphql_test_harness.py --list-scenarios`; `python -m unittest tests.core.test_graphql_test_harness`; `make test-core`; `make proof`.
- Known limits: Does not prove production correctness, exhaustive input coverage, or final harness maturity. This dossier maps current source, tests, and ratchet state as of this batch; it is expected to change.
- Related harnesses: `core/tracing`, `core/queue`, `core/search_relevance`.

## Batch 12 closeout

Docs and source surfaces checked for this batch:

- `HARNESS_INVENTORY.md`
- `cards/teeth_ratchet.json`
- relevant `harnesses/**` files for the mapped entries
- relevant paired `tests/**` files for the mapped entries
- `docs/harness-map/batch-12-tracing-queue-search-rag-graphql.md`
- `docs/harness-map/README.md`

Scope note: this PR is docs-only. It does not change harness behavior, tests, workflows, hooks, dependencies, dashboard code, generated status files, TEETH status, or central-map consolidation.
