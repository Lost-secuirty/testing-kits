#!/usr/bin/env python3
"""
search_relevance_test_harness.py — IR ranking metrics + analyzer corner cases.
===============================================================================

Pure-stdlib. Zero external dependencies. No model.

Search/ranking changes regress quietly: recall@k or NDCG drops against a fixed
judgment set, or a tokenizer/analyzer bug (case, accents, plurals, stop-words,
NFKC width, CJK no-space segmentation) makes relevant docs unretrievable. This
harness computes classic IR metrics (recall@k, precision@k, MRR, NDCG with
graded relevance) over fixed `(query, judgments)` sets and runs an analyzer
corner-case oracle, then proves a reversed ranker falls below the NDCG floor and
a no-fold analyzer fails matches a correct analyzer makes.

Distinct from `llm_eval` / `rag_eval` (LLM answer quality) and `pagination`
(cursor consistency). In-process; port 19320 is reserved but no server is run.

Usage:
  python harnesses/core/search_relevance_test_harness.py --self-test
  python harnesses/core/search_relevance_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import math
import re
import sys
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path as _Path

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import Mutant, Report, Teeth

RESERVED_PORT = 19320  # documented; harness runs in-process

TOKEN_RE = re.compile(r"[぀-ヿ一-鿿]|[^\W_]+", re.UNICODE)
STOPWORDS = frozenset({"the", "a", "an", "of", "and", "to", "in", "is", "it",
                       "on", "for", "with", "i", "am", "are", "this", "that"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Doc:
    doc_id: str
    text: str


@dataclass(frozen=True)
class Judgment:
    query: str
    doc_id: str
    grade: int  # 0..3 graded relevance


@dataclass(frozen=True)
class QuerySet:
    query: str
    judgments: tuple[Judgment, ...]


# 6 topically-disjoint queries; each has a grade-3/2/1 doc whose distinct
# query-term overlap equals its grade, so the lexical ranker reproduces the
# ideal ordering. Two pure distractors round the corpus to 20 docs.
_TOPICS: list[tuple[str, list[str]]] = [
    ("python unit testing", ["python", "unit", "testing"]),
    ("machine learning models", ["machine", "learning", "models"]),
    ("espresso coffee beans", ["espresso", "coffee", "beans"]),
    ("garden tomato soil", ["garden", "tomato", "soil"]),
    ("tcp network packet", ["tcp", "network", "packet"]),
    ("invoice tax payment", ["invoice", "tax", "payment"]),
]


def _build_corpus() -> tuple[list[Doc], list[QuerySet]]:
    docs: list[Doc] = []
    qsets: list[QuerySet] = []
    for ti, (query, terms) in enumerate(_TOPICS):
        d3 = Doc(f"d{ti}_g3", " ".join(terms) + " reference guide")
        d2 = Doc(f"d{ti}_g2", " ".join(terms[:2]) + " overview")
        d1 = Doc(f"d{ti}_g1", terms[0] + " notes")
        docs.extend([d3, d2, d1])
        qsets.append(QuerySet(query, (
            Judgment(query, d3.doc_id, 3),
            Judgment(query, d2.doc_id, 2),
            Judgment(query, d1.doc_id, 1),
        )))
    docs.append(Doc("dx_1", "lorem ipsum dolor sit amet consectetur"))
    docs.append(Doc("dx_2", "quick brown fox jumps over the lazy hound"))
    return docs, qsets


DOCS, QUERY_SETS = _build_corpus()

# (term, doc_text, should_match) under a fully-folding analyzer.
ANALYZER_CASES: list[tuple[str, str, bool]] = [
    ("Cat", "the cat sat", True),                 # casefold
    ("café", "i love Cafe culture", True),        # accent fold + casefold
    ("models", "one model here", True),           # plural stem
    ("cats", "a single cat", True),               # plural stem
    ("the", "the dog runs", False),               # stop-word dropped
    ("ＡＢＣ", "abc test", True),                   # NFKC fullwidth
    ("猫", "我有一只猫", True),                      # CJK segmentation
    ("zebra", "the cat sat", False),              # genuinely unrelated
]


# ---------------------------------------------------------------------------
# Config + analyzer
# ---------------------------------------------------------------------------


@dataclass
class SearchConfig:
    k: int = 10
    recall_floor: float = 0.80
    mrr_floor: float = 0.70
    ndcg_floor: float = 0.80
    fold_case: bool = True
    fold_accents: bool = True
    stem: bool = True
    strip_stopwords: bool = True


def _strip_accents(s: str) -> str:
    nfd = unicodedata.normalize("NFD", s)
    return "".join(c for c in nfd if not unicodedata.combining(c))


def analyze(text: str, config: SearchConfig | None = None) -> list[str]:
    config = config or SearchConfig()
    t = unicodedata.normalize("NFKC", text)
    if config.fold_case:
        t = t.casefold()
    if config.fold_accents:
        t = _strip_accents(t)
    out: list[str] = []
    for tok in TOKEN_RE.findall(t):
        if config.strip_stopwords and tok in STOPWORDS:
            continue
        if config.stem and len(tok) > 3 and tok.endswith("s"):
            tok = tok[:-1]
        out.append(tok)
    return out


def no_fold_analyze(text: str) -> list[str]:
    """A broken analyzer that skips case/accent/stem/stopword folding."""
    return analyze(text, SearchConfig(fold_case=False, fold_accents=False,
                                      stem=False, strip_stopwords=False))


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


def search(query: str, docs: list[Doc], config: SearchConfig | None = None) -> list[str]:
    """Rank doc_ids by distinct query-term overlap; stable tie-break by doc_id."""
    config = config or SearchConfig()
    q_tokens = set(analyze(query, config))
    scored: list[tuple[int, str]] = []
    for d in docs:
        overlap = len(q_tokens & set(analyze(d.text, config)))
        if overlap > 0:
            scored.append((overlap, d.doc_id))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [doc_id for _, doc_id in scored]


def reversed_search(query: str, docs: list[Doc],
                    config: SearchConfig | None = None) -> list[str]:
    """A broken ranker that returns worst-overlap-first."""
    return list(reversed(search(query, docs, config)))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def recall_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ranked[:k]) & relevant) / len(relevant)


def precision_at_k(ranked: list[str], relevant: set[str], k: int) -> float:
    denom = min(k, len(ranked))
    if denom == 0:
        return 0.0
    return len(set(ranked[:k]) & relevant) / denom


def mrr(ranked: list[str], relevant: set[str]) -> float:
    for i, doc_id in enumerate(ranked):
        if doc_id in relevant:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(ranked: list[str], grades: dict[str, int], k: int, binary: bool) -> float:
    total = 0.0
    for i, doc_id in enumerate(ranked[:k]):
        g = grades.get(doc_id, 0)
        gain = (1.0 if g > 0 else 0.0) if binary else (2.0 ** g - 1.0)
        total += gain / math.log2(i + 2)
    return total


def ndcg_at_k(ranked: list[str], grades: dict[str, int], k: int,
              binary: bool = False) -> float:
    ideal = sorted(grades.values(), reverse=True)
    idcg = 0.0
    for i, g in enumerate(ideal[:k]):
        gain = (1.0 if g > 0 else 0.0) if binary else (2.0 ** g - 1.0)
        idcg += gain / math.log2(i + 2)
    if idcg == 0:
        return 0.0
    return _dcg(ranked, grades, k, binary) / idcg


# ---------------------------------------------------------------------------
# Evaluation report
# ---------------------------------------------------------------------------


@dataclass
class RelevanceReport:
    n_queries: int
    mean_recall_at_k: float
    mean_precision_at_k: float
    mean_mrr: float
    mean_ndcg_at_k: float
    analyzer_failures: int

    def meets_floors(self, config: SearchConfig) -> bool:
        return (
            self.mean_recall_at_k >= config.recall_floor
            and self.mean_mrr >= config.mrr_floor
            and self.mean_ndcg_at_k >= config.ndcg_floor
            and self.analyzer_failures == 0
        )


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


SearchFn = Callable[[str, list[Doc], SearchConfig | None], list[str]]


def evaluate(query_sets: list[QuerySet], docs: list[Doc],
             config: SearchConfig | None = None,
             search_fn: SearchFn = search,
             analyze_fn: Callable[[str], list[str]] | None = None) -> RelevanceReport:
    config = config or SearchConfig()
    analyze_fn = analyze_fn or (lambda t: analyze(t, config))
    recalls, precs, mrrs, ndcgs = [], [], [], []
    for qs in query_sets:
        relevant = {j.doc_id for j in qs.judgments if j.grade > 0}
        grades = {j.doc_id: j.grade for j in qs.judgments}
        ranked = search_fn(qs.query, docs, config)
        recalls.append(recall_at_k(ranked, relevant, config.k))
        precs.append(precision_at_k(ranked, relevant, config.k))
        mrrs.append(mrr(ranked, relevant))
        ndcgs.append(ndcg_at_k(ranked, grades, config.k))
    return RelevanceReport(
        n_queries=len(query_sets),
        mean_recall_at_k=_mean(recalls),
        mean_precision_at_k=_mean(precs),
        mean_mrr=_mean(mrrs),
        mean_ndcg_at_k=_mean(ndcgs),
        analyzer_failures=analyzer_failures(ANALYZER_CASES, analyze_fn),
    )


def analyzer_failures(cases: list[tuple[str, str, bool]],
                      analyze_fn: Callable[[str], list[str]]) -> int:
    fails = 0
    for term, doc_text, should_match in cases:
        matched = bool(set(analyze_fn(term)) & set(analyze_fn(doc_text)))
        if matched != should_match:
            fails += 1
    return fails


# ---------------------------------------------------------------------------
# TEETH: frozen search-relevance audits + planted ranking/analyzer defects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchRelevanceAuditCase:
    name: str
    check: str
    expected_events: tuple[str, ...]


SEARCH_RELEVANCE_AUDIT_CORPUS: tuple[SearchRelevanceAuditCase, ...] = (
    SearchRelevanceAuditCase(
        name="lexical_ranker_meets_relevance_floors",
        check="benchmark",
        expected_events=("meets_floors",),
    ),
    SearchRelevanceAuditCase(
        name="analyzer_handles_fold_and_segmentation_cases",
        check="analyzer",
        expected_events=("analyzer_clean",),
    ),
    SearchRelevanceAuditCase(
        name="tie_break_orders_by_doc_id",
        check="tie_break",
        expected_events=("stable_tiebreak",),
    ),
    SearchRelevanceAuditCase(
        name="empty_query_returns_no_results",
        check="empty_query",
        expected_events=("empty_query_zero",),
    ),
)


def oracle_search_relevance_audit(case: SearchRelevanceAuditCase) -> tuple[str, ...]:
    cfg = SearchConfig()
    if case.check == "benchmark":
        rep = evaluate(QUERY_SETS, DOCS, cfg)
        return ("meets_floors",) if rep.meets_floors(cfg) else ()
    if case.check == "analyzer":
        return ("analyzer_clean",) if analyzer_failures(ANALYZER_CASES, analyze) == 0 else ()
    if case.check == "tie_break":
        docs = [Doc("zeta", "alpha"), Doc("alpha", "alpha"), Doc("mike", "alpha")]
        return ("stable_tiebreak",) if search("alpha", docs) == ["alpha", "mike", "zeta"] else ()
    if case.check == "empty_query":
        ranked = search("", DOCS)
        return ("empty_query_zero",) if ranked == [] and mrr(ranked, {"r"}) < 1e-12 else ()
    raise ValueError(f"unknown search relevance audit check: {case.check}")


def reversed_ranker_search_auditor(case: SearchRelevanceAuditCase) -> tuple[str, ...]:
    if case.check != "benchmark":
        return oracle_search_relevance_audit(case)
    cfg = SearchConfig()
    rep = evaluate(QUERY_SETS, DOCS, cfg, search_fn=reversed_search)
    return ("meets_floors",) if rep.meets_floors(cfg) else ()


def no_fold_search_auditor(case: SearchRelevanceAuditCase) -> tuple[str, ...]:
    if case.check != "analyzer":
        return oracle_search_relevance_audit(case)
    return ("analyzer_clean",) if analyzer_failures(ANALYZER_CASES, no_fold_analyze) == 0 else ()


def unstable_tiebreak_search_auditor(case: SearchRelevanceAuditCase) -> tuple[str, ...]:
    if case.check != "tie_break":
        return oracle_search_relevance_audit(case)
    docs = [Doc("zeta", "alpha"), Doc("alpha", "alpha"), Doc("mike", "alpha")]
    ranked = sorted(search("alpha", docs), reverse=True)
    return ("stable_tiebreak",) if ranked == ["alpha", "mike", "zeta"] else ()


def empty_query_returns_all_auditor(case: SearchRelevanceAuditCase) -> tuple[str, ...]:
    if case.check != "empty_query":
        return oracle_search_relevance_audit(case)
    ranked = [doc.doc_id for doc in DOCS]
    return ("empty_query_zero",) if ranked == [] and mrr(ranked, {"r"}) < 1e-12 else ()


def prove(impl: Callable[[SearchRelevanceAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in SEARCH_RELEVANCE_AUDIT_CORPUS)


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_search_relevance_audit"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_search_relevance_audit,
    mutants=(
        Mutant("reversed_ranker_search_auditor", reversed_ranker_search_auditor,
               "ranks worst-overlap results first and falls below NDCG floors"),
        Mutant("no_fold_search_auditor", no_fold_search_auditor,
               "skips case, accent, stem, stopword, NFKC, and CJK analyzer handling"),
        Mutant("unstable_tiebreak_search_auditor", unstable_tiebreak_search_auditor,
               "uses an unstable tie-break order for equal-overlap documents"),
        Mutant("empty_query_returns_all_auditor", empty_query_returns_all_auditor,
               "returns documents for an empty query instead of a zero-result baseline"),
    ),
    corpus_size=len(SEARCH_RELEVANCE_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen ranking-floor, analyzer, tie-break, and empty-query relevance corpus.",
)


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

_EPS = 1e-9


@dataclass
class RelCheck:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> RelCheck:
    return RelCheck(name, bool(cond), detail)


def s_recall_at_k_perfect_ranking() -> RelCheck:
    v = recall_at_k(["r1", "r2"], {"r1", "r2"}, 10)
    return _chk("recall_at_k_perfect_ranking", abs(v - 1.0) < _EPS, f"{v}")


def s_recall_at_k_partial() -> RelCheck:
    v = recall_at_k(["r1", "x"], {"r1", "r2"}, 10)
    return _chk("recall_at_k_partial", abs(v - 0.5) < _EPS, f"{v}")


def s_recall_at_1_vs_recall_at_5() -> RelCheck:
    ranked = ["x", "r1", "r2", "y", "z"]
    rel = {"r1", "r2"}
    return _chk("recall_at_1_vs_recall_at_5",
                recall_at_k(ranked, rel, 1) < recall_at_k(ranked, rel, 5),
                f"@1={recall_at_k(ranked, rel, 1)} @5={recall_at_k(ranked, rel, 5)}")


def s_precision_at_k_with_irrelevant_in_topk() -> RelCheck:
    v = precision_at_k(["r1", "x", "r2"], {"r1", "r2"}, 3)
    return _chk("precision_at_k_with_irrelevant_in_topk", abs(v - 2 / 3) < _EPS, f"{v}")


def s_mrr_first_relevant_rank_1() -> RelCheck:
    v = mrr(["r", "x"], {"r"})
    return _chk("mrr_first_relevant_rank_1", abs(v - 1.0) < _EPS, f"{v}")


def s_mrr_first_relevant_rank_3() -> RelCheck:
    v = mrr(["x", "y", "r"], {"r"})
    return _chk("mrr_first_relevant_rank_3", abs(v - 1 / 3) < _EPS, f"{v}")


def s_mrr_no_relevant_is_zero() -> RelCheck:
    v = mrr(["x", "y"], {"r"})
    return _chk("mrr_no_relevant_is_zero", v == 0.0, f"{v}")


def s_ndcg_ideal_ranking_is_1() -> RelCheck:
    grades = {"a": 3, "b": 2, "c": 1}
    v = ndcg_at_k(["a", "b", "c"], grades, 10)
    return _chk("ndcg_ideal_ranking_is_1", abs(v - 1.0) < _EPS, f"{v}")


def s_ndcg_penalizes_low_ranked_high_grade() -> RelCheck:
    grades = {"a": 3, "b": 2, "c": 1}
    v = ndcg_at_k(["c", "b", "a"], grades, 10)
    return _chk("ndcg_penalizes_low_ranked_high_grade", v < 0.90, f"{v:.4f}")


def s_ndcg_graded_vs_binary_differs() -> RelCheck:
    grades = {"a": 1, "b": 3, "c": 2}
    ranked = ["a", "b", "c"]
    graded = ndcg_at_k(ranked, grades, 10, binary=False)
    binary = ndcg_at_k(ranked, grades, 10, binary=True)
    return _chk("ndcg_graded_vs_binary_differs", abs(graded - binary) > 1e-6,
                f"graded={graded:.4f} binary={binary:.4f}")


def _analyzer_case(name: str, term: str, doc: str, should: bool) -> RelCheck:
    matched = bool(set(analyze(term)) & set(analyze(doc)))
    return _chk(name, matched == should, f"matched={matched}")


def s_analyzer_casefold_matches() -> RelCheck:
    return _analyzer_case("analyzer_casefold_matches", "Cat", "the cat sat", True)


def s_analyzer_accent_fold_matches() -> RelCheck:
    return _analyzer_case("analyzer_accent_fold_matches", "café", "Cafe time", True)


def s_analyzer_plural_stem_matches() -> RelCheck:
    return _analyzer_case("analyzer_plural_stem_matches", "models", "one model", True)


def s_analyzer_stopword_dropped() -> RelCheck:
    return _chk("analyzer_stopword_dropped", analyze("the") == [], f"{analyze('the')}")


def s_analyzer_nfkc_normalizes_fullwidth() -> RelCheck:
    return _analyzer_case("analyzer_nfkc_normalizes_fullwidth", "ＡＢＣ", "abc test", True)


def s_analyzer_cjk_no_space_segmentation() -> RelCheck:
    return _analyzer_case("analyzer_cjk_no_space_segmentation", "猫", "我有一只猫", True)


def s_tokenizer_deterministic() -> RelCheck:
    a = analyze("Running models in the café 猫")
    b = analyze("Running models in the café 猫")
    r1 = search("python unit testing", DOCS)
    r2 = search("python unit testing", DOCS)
    return _chk("tokenizer_deterministic", a == b and r1 == r2, "")


def s_empty_query_zero_metrics() -> RelCheck:
    ranked = search("", DOCS)
    return _chk("empty_query_zero_metrics",
                ranked == [] and mrr(ranked, {"r"}) == 0.0, f"ranked={ranked}")


def s_tie_break_is_stable() -> RelCheck:
    docs = [Doc("zeta", "alpha"), Doc("alpha", "alpha"), Doc("mike", "alpha")]
    ranked = search("alpha", docs)
    return _chk("tie_break_is_stable", ranked == ["alpha", "mike", "zeta"], f"{ranked}")


def s_oracle_meets_floors() -> RelCheck:
    cfg = SearchConfig()
    rep = evaluate(QUERY_SETS, DOCS, cfg)
    return _chk("oracle_meets_floors", rep.meets_floors(cfg),
                f"recall={rep.mean_recall_at_k:.3f} mrr={rep.mean_mrr:.3f} "
                f"ndcg={rep.mean_ndcg_at_k:.3f} af={rep.analyzer_failures}")


def s_reversed_ranker_below_ndcg_floor() -> RelCheck:
    cfg = SearchConfig()
    rep = evaluate(QUERY_SETS, DOCS, cfg, search_fn=reversed_search)
    return _chk("reversed_ranker_below_ndcg_floor", rep.mean_ndcg_at_k < cfg.ndcg_floor,
                f"ndcg={rep.mean_ndcg_at_k:.4f}")


def s_no_fold_analyzer_misses_accent() -> RelCheck:
    af = analyzer_failures(ANALYZER_CASES, no_fold_analyze)
    return _chk("no_fold_analyzer_misses_accent", af >= 1, f"failures={af}")


SCENARIOS: dict[str, Callable[[], RelCheck]] = {
    f.__name__[2:]: f
    for f in [
        s_recall_at_k_perfect_ranking,
        s_recall_at_k_partial,
        s_recall_at_1_vs_recall_at_5,
        s_precision_at_k_with_irrelevant_in_topk,
        s_mrr_first_relevant_rank_1,
        s_mrr_first_relevant_rank_3,
        s_mrr_no_relevant_is_zero,
        s_ndcg_ideal_ranking_is_1,
        s_ndcg_penalizes_low_ranked_high_grade,
        s_ndcg_graded_vs_binary_differs,
        s_analyzer_casefold_matches,
        s_analyzer_accent_fold_matches,
        s_analyzer_plural_stem_matches,
        s_analyzer_stopword_dropped,
        s_analyzer_nfkc_normalizes_fullwidth,
        s_analyzer_cjk_no_space_segmentation,
        s_tokenizer_deterministic,
        s_empty_query_zero_metrics,
        s_tie_break_is_stable,
        s_oracle_meets_floors,
        s_reversed_ranker_below_ndcg_floor,
        s_no_fold_analyzer_misses_accent,
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
            print(f"  {mark}  {r.name:42s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    report = Report("core/search_relevance")
    for case in SEARCH_RELEVANCE_AUDIT_CORPUS:
        report.add(
            f"oracle_search_relevance_audit:{case.name}",
            list(case.expected_events),
            list(oracle_search_relevance_audit(case)),
        )
    report.assert_teeth(TEETH)
    if not report.passed:
        return report.emit()
    print(f"OK: {len(results)} scenarios passed.")
    return report.emit()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Search-relevance IR-metrics harness")
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
