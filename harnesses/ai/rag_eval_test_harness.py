#!/usr/bin/env python3
"""
rag_eval_test_harness.py — RAG retrieval recall, citation faithfulness, grounding, overflow.
=============================================================================================

Pure-stdlib. Zero external dependencies. No model.

Retrieval-augmented answers fail in ways a "reads fluent" check misses: the gold
passage is never retrieved (low recall), the answer cites a passage it didn't
retrieve or that doesn't support the claim (citation infidelity), it asserts
facts absent from any retrieved context (ungrounded), or the context overflows
the window and tail passages are silently dropped (grounding degrades). This
harness scores those four axes over fixed cases with a deterministic retriever,
and proves a keyword-only retriever, a truncating retriever, and a citation
fabricator each break a floor.

Distinct from `ai/llm_eval` (answer-quality graders, no retrieval) and
`ai/prompt_injection` (safety corpus). In-process, no server.

Usage:
  python harnesses/ai/rag_eval_test_harness.py --self-test
  python harnesses/ai/rag_eval_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Callable, Optional

_TOK = re.compile(r"[a-z0-9]+")


def _tok(text: str) -> list[str]:
    return _TOK.findall(text.casefold())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Passage:
    doc_id: str
    text: str
    tokens: int = 0

    def __post_init__(self) -> None:
        if self.tokens == 0:
            object.__setattr__(self, "tokens", len(self.text.split()))


@dataclass(frozen=True)
class RagCase:
    query: str
    gold_doc_ids: tuple[str, ...]
    answer: str
    cited_doc_ids: tuple[str, ...]
    supported_claims: tuple[str, ...]


_TOPICS: list[tuple[str, list[str]]] = [
    ("neural network training", ["neural", "network", "training"]),
    ("ocean current salinity", ["ocean", "current", "salinity"]),
    ("roman empire history", ["roman", "empire", "history"]),
    ("guitar chord theory", ["guitar", "chord", "theory"]),
    ("vaccine immune response", ["vaccine", "immune", "response"]),
    ("compiler syntax parsing", ["compiler", "syntax", "parsing"]),
]


def _build_corpus() -> tuple[list[Passage], list[RagCase]]:
    passages: list[Passage] = []
    cases: list[RagCase] = []
    for i, (query, t) in enumerate(_TOPICS):
        ga = Passage(f"p{i}_a", f"{t[0]} {t[1]} {t[2]} explained in detail")
        gb = Passage(f"p{i}_b", f"{t[0]} {t[1]} fact{i} appendix")  # fact{i} only here
        passages.extend([ga, gb])
        cases.append(RagCase(
            query=query,
            gold_doc_ids=(ga.doc_id, gb.doc_id),
            answer=f"{t[0]} {t[1]} {t[2]}; also fact{i}.",
            cited_doc_ids=(ga.doc_id, gb.doc_id),
            supported_claims=(f"{t[0]} {t[1]} {t[2]}", f"fact{i}"),
        ))
    for j in range(8):
        passages.append(Passage(f"dx{j}", f"lorem ipsum dolor filler{j} unrelated text"))
    return passages, cases


CORPUS, CASES = _build_corpus()


# ---------------------------------------------------------------------------
# Config + retrievers
# ---------------------------------------------------------------------------


@dataclass
class RagConfig:
    k: int = 5
    context_window_tokens: int = 512
    recall_floor: float = 0.80
    faithfulness_floor: float = 0.90
    grounding_floor: float = 0.80


def retrieve(query: str, corpus: list[Passage], k: int) -> list[Passage]:
    """Deterministic lexical top-k by distinct query-term overlap; dedup by id."""
    q = set(_tok(query))
    scored: list[tuple[int, Passage]] = []
    seen: set[str] = set()
    for p in corpus:
        if p.doc_id in seen:
            continue
        seen.add(p.doc_id)
        overlap = len(q & set(_tok(p.text)))
        if overlap > 0:
            scored.append((overlap, p))
    scored.sort(key=lambda x: (-x[0], x[1].doc_id))
    return [p for _, p in scored[:k]]


def keyword_only_retrieve(query: str, corpus: list[Passage], k: int) -> list[Passage]:
    """Broken: requires ALL query terms present (AND) → misses partial-overlap golds."""
    q = set(_tok(query))
    out = [p for p in corpus if q <= set(_tok(p.text))]
    return out[:k]


def truncating_retrieve(query: str, corpus: list[Passage], k: int) -> list[Passage]:
    """Broken: silently keeps only the top passage, dropping the rest."""
    return retrieve(query, corpus, k)[:1]


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def recall_at_k(retrieved_ids: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    return len(set(retrieved_ids[:k]) & gold) / len(gold)


def _supports(text: str, claims: tuple[str, ...]) -> bool:
    toks = set(_tok(text))
    return any(set(_tok(c)) <= toks for c in claims)


def citation_audit(cited_ids: tuple[str, ...], retrieved_ids: list[str],
                   corpus_map: dict[str, Passage],
                   claims: tuple[str, ...]) -> tuple[float, int]:
    """Return (faithfulness, fabricated_count). A citation is faithful only if it was
    retrieved AND its passage supports a claim; fabricated if it was not retrieved."""
    if not cited_ids:
        return 1.0, 0
    faithful = 0
    fabricated = 0
    for c in cited_ids:
        if c not in retrieved_ids:
            fabricated += 1
            continue
        doc = corpus_map.get(c)
        if doc and _supports(doc.text, claims):
            faithful += 1
    return faithful / len(cited_ids), fabricated


def grounding_audit(claims: tuple[str, ...],
                    kept_passages: list[Passage]) -> tuple[float, int]:
    """Return (grounding, ungrounded_count) over the post-overflow context."""
    if not claims:
        return 1.0, 0
    ctx: set[str] = set()
    for p in kept_passages:
        ctx |= set(_tok(p.text))
    grounded = sum(1 for c in claims if set(_tok(c)) <= ctx)
    return grounded / len(claims), len(claims) - grounded


def context_overflow(passages: list[Passage],
                     window_tokens: int) -> tuple[list[Passage], list[str]]:
    """Greedy-pack passages in order under a token budget; return (kept, dropped_ids)."""
    kept: list[Passage] = []
    dropped: list[str] = []
    running = 0
    for p in passages:
        if running + p.tokens <= window_tokens:
            kept.append(p)
            running += p.tokens
        else:
            dropped.append(p.doc_id)
    return kept, dropped


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class RagReport:
    n_cases: int
    mean_recall_at_k: float
    mean_faithfulness: float
    mean_grounding: float
    overflow_truncations: int
    ungrounded_claims: int
    fabricated_citations: int

    def meets_floors(self, config: RagConfig) -> bool:
        return (
            self.mean_recall_at_k >= config.recall_floor
            and self.mean_faithfulness >= config.faithfulness_floor
            and self.mean_grounding >= config.grounding_floor
        )


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


Retriever = Callable[[str, list[Passage], int], list[Passage]]
CitedFn = Callable[[RagCase, list[str]], tuple[str, ...]]


def evaluate(cases: list[RagCase], corpus: list[Passage],
             config: RagConfig | None = None,
             retriever: Retriever = retrieve,
             cited_fn: Optional[CitedFn] = None) -> RagReport:
    config = config or RagConfig()
    cmap = {p.doc_id: p for p in corpus}
    recalls, faiths, grounds = [], [], []
    overflow = ungrounded = fabricated = 0
    for case in cases:
        retrieved = retriever(case.query, corpus, config.k)
        rids = [p.doc_id for p in retrieved]
        kept, dropped = context_overflow(retrieved, config.context_window_tokens)
        overflow += len(dropped)
        recalls.append(recall_at_k(rids, set(case.gold_doc_ids), config.k))
        cited = cited_fn(case, rids) if cited_fn else case.cited_doc_ids
        f, fab = citation_audit(cited, rids, cmap, case.supported_claims)
        faiths.append(f)
        fabricated += fab
        g, ung = grounding_audit(case.supported_claims, kept)
        grounds.append(g)
        ungrounded += ung
    return RagReport(
        n_cases=len(cases),
        mean_recall_at_k=_mean(recalls),
        mean_faithfulness=_mean(faiths),
        mean_grounding=_mean(grounds),
        overflow_truncations=overflow,
        ungrounded_claims=ungrounded,
        fabricated_citations=fabricated,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

_EPS = 1e-9


@dataclass
class RagCheck:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> RagCheck:
    return RagCheck(name, bool(cond), detail)


def _cmap() -> dict[str, Passage]:
    return {p.doc_id: p for p in CORPUS}


def s_oracle_meets_floors() -> RagCheck:
    cfg = RagConfig()
    rep = evaluate(CASES, CORPUS, cfg)
    return _chk("oracle_meets_floors", rep.meets_floors(cfg),
                f"recall={rep.mean_recall_at_k:.3f} faith={rep.mean_faithfulness:.3f} "
                f"ground={rep.mean_grounding:.3f}")


def s_retrieval_recall_perfect_when_gold_top_k() -> RagCheck:
    rep = evaluate(CASES, CORPUS, RagConfig())
    return _chk("retrieval_recall_perfect_when_gold_top_k",
                abs(rep.mean_recall_at_k - 1.0) < _EPS, f"{rep.mean_recall_at_k}")


def s_recall_drops_at_low_k() -> RagCheck:
    v = recall_at_k(["g_a", "g_b"], {"g_a", "g_b"}, 1)
    return _chk("recall_drops_at_low_k", abs(v - 0.5) < _EPS, f"{v}")


def s_recall_at_1_vs_recall_at_5() -> RagCheck:
    ranked = ["x", "g_a", "g_b"]
    gold = {"g_a", "g_b"}
    return _chk("recall_at_1_vs_recall_at_5",
                recall_at_k(ranked, gold, 1) < recall_at_k(ranked, gold, 5), "")


def s_citation_faithful_all_cited_retrieved() -> RagCheck:
    f, fab = citation_audit(("p0_a", "p0_b"), ["p0_a", "p0_b"], _cmap(),
                            ("neural network training", "fact0"))
    return _chk("citation_faithful_all_cited_retrieved",
                abs(f - 1.0) < _EPS and fab == 0, f"faith={f} fab={fab}")


def s_citation_to_unretrieved_doc_flagged() -> RagCheck:
    f, fab = citation_audit(("p0_a", "ghost"), ["p0_a"], _cmap(),
                            ("neural network training",))
    return _chk("citation_to_unretrieved_doc_flagged", fab >= 1 and f < 1.0,
                f"faith={f} fab={fab}")


def s_citation_to_irrelevant_doc_flagged() -> RagCheck:
    # dx0 is retrieved here but does not support the claim → unfaithful, not fabricated
    f, fab = citation_audit(("dx0",), ["dx0"], _cmap(), ("neural network training",))
    return _chk("citation_to_irrelevant_doc_flagged", f < 1.0 and fab == 0,
                f"faith={f} fab={fab}")


def s_answer_fully_grounded() -> RagCheck:
    cmap = _cmap()
    g, ung = grounding_audit(("neural network training", "fact0"),
                             [cmap["p0_a"], cmap["p0_b"]])
    return _chk("answer_fully_grounded", abs(g - 1.0) < _EPS and ung == 0, f"g={g}")


def s_answer_with_hallucinated_claim_flagged() -> RagCheck:
    cmap = _cmap()
    g, ung = grounding_audit(("wizard dragon spell",), [cmap["p0_a"], cmap["p0_b"]])
    return _chk("answer_with_hallucinated_claim_flagged", g == 0.0 and ung >= 1,
                f"g={g} ung={ung}")


def s_partial_grounding_scored_fractionally() -> RagCheck:
    cmap = _cmap()
    g, ung = grounding_audit(("neural network training", "wizard dragon"),
                             [cmap["p0_a"]])
    return _chk("partial_grounding_scored_fractionally", abs(g - 0.5) < _EPS and ung == 1,
                f"g={g} ung={ung}")


def s_context_fits_window_no_truncation() -> RagCheck:
    cmap = _cmap()
    kept, dropped = context_overflow([cmap["p0_a"], cmap["p0_b"]], 512)
    return _chk("context_fits_window_no_truncation", dropped == [] and len(kept) == 2, "")


def s_context_overflow_truncates_tail_passages() -> RagCheck:
    ps = [Passage("big1", "x", tokens=400), Passage("big2", "y", tokens=400)]
    kept, dropped = context_overflow(ps, 512)
    return _chk("context_overflow_truncates_tail_passages",
                dropped == ["big2"] and [p.doc_id for p in kept] == ["big1"], f"{dropped}")


def s_overflow_drops_gold_passage_degrades_grounding() -> RagCheck:
    ga = Passage("g_a", "neural network training explained", tokens=400)
    gb = Passage("g_b", "fact0 appendix detail here", tokens=400)
    kept, dropped = context_overflow([ga, gb], 512)
    g, ung = grounding_audit(("neural network training", "fact0"), kept)
    return _chk("overflow_drops_gold_passage_degrades_grounding",
                "g_b" in dropped and abs(g - 0.5) < _EPS, f"dropped={dropped} g={g}")


def s_empty_retrieval_zero_recall() -> RagCheck:
    retrieved = retrieve("zzzznonsense qqqq", CORPUS, 5)
    r = recall_at_k([p.doc_id for p in retrieved], {"p0_a"}, 5)
    return _chk("empty_retrieval_zero_recall", retrieved == [] and r == 0.0, "")


def s_duplicate_passages_deduped() -> RagCheck:
    dup = [Passage("d", "neural network"), Passage("d", "neural network")]
    retrieved = retrieve("neural network", dup, 5)
    return _chk("duplicate_passages_deduped", len(retrieved) == 1, f"n={len(retrieved)}")


def s_tokenizer_counts_tokens_consistently() -> RagCheck:
    a = _tok("Neural Network TRAINING 123")
    b = _tok("Neural Network TRAINING 123")
    r1 = [p.doc_id for p in retrieve("ocean current", CORPUS, 5)]
    r2 = [p.doc_id for p in retrieve("ocean current", CORPUS, 5)]
    return _chk("tokenizer_counts_tokens_consistently", a == b and r1 == r2, "")


def s_keyword_baseline_below_recall_floor() -> RagCheck:
    cfg = RagConfig()
    rep = evaluate(CASES, CORPUS, cfg, retriever=keyword_only_retrieve)
    return _chk("keyword_baseline_below_recall_floor", rep.mean_recall_at_k < cfg.recall_floor,
                f"recall={rep.mean_recall_at_k:.3f}")


def s_truncating_retriever_degrades_grounding() -> RagCheck:
    cfg = RagConfig()
    rep = evaluate(CASES, CORPUS, cfg, retriever=truncating_retrieve)
    return _chk("truncating_retriever_degrades_grounding",
                rep.mean_grounding < cfg.grounding_floor or rep.mean_recall_at_k < cfg.recall_floor,
                f"ground={rep.mean_grounding:.3f} recall={rep.mean_recall_at_k:.3f}")


def s_citation_fabricator_below_faithfulness_floor() -> RagCheck:
    cfg = RagConfig()
    rep = evaluate(CASES, CORPUS, cfg,
                   cited_fn=lambda case, rids: case.cited_doc_ids + ("ghost_doc",))
    return _chk("citation_fabricator_below_faithfulness_floor",
                rep.fabricated_citations >= len(CASES)
                and rep.mean_faithfulness < cfg.faithfulness_floor,
                f"fab={rep.fabricated_citations} faith={rep.mean_faithfulness:.3f}")


SCENARIOS: dict[str, Callable[[], RagCheck]] = {
    f.__name__[2:]: f
    for f in [
        s_oracle_meets_floors,
        s_retrieval_recall_perfect_when_gold_top_k,
        s_recall_drops_at_low_k,
        s_recall_at_1_vs_recall_at_5,
        s_citation_faithful_all_cited_retrieved,
        s_citation_to_unretrieved_doc_flagged,
        s_citation_to_irrelevant_doc_flagged,
        s_answer_fully_grounded,
        s_answer_with_hallucinated_claim_flagged,
        s_partial_grounding_scored_fractionally,
        s_context_fits_window_no_truncation,
        s_context_overflow_truncates_tail_passages,
        s_overflow_drops_gold_passage_degrades_grounding,
        s_empty_retrieval_zero_recall,
        s_duplicate_passages_deduped,
        s_tokenizer_counts_tokens_consistently,
        s_keyword_baseline_below_recall_floor,
        s_truncating_retriever_degrades_grounding,
        s_citation_fabricator_below_faithfulness_floor,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:48s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="RAG retrieval/citation/grounding eval harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test:
        return _run_self_test(verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
