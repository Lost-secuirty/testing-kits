#!/usr/bin/env python3
"""
drift_detection_test_harness.py — Model/embedding drift: PSI, KL/JS, Hellinger, rank, version.
===============================================================================================

Pure-stdlib. Zero external dependencies. No numpy. No model.

A model upgrade can silently regress quality: the embedding distribution shifts
(PSI/KL/JS/Hellinger climb), the embedding-space centroid moves, query-document
cosine similarity collapses, the top-k neighbor ranking churns, or the query is
embedded with a different model version than the index. Detectors get this wrong
in characteristic ways: no epsilon floor on empty bins, KL with the arguments
swapped, an averaged (washed-out) centroid distance, an unnormalized cosine, set
overlap used in place of a rank correlation, ignoring version metadata, or alerting
on stable data. This harness computes each metric by hand over fixed fixtures and
proves seven buggy detectors each miss real drift or fire on stable data.

In-process, no server. All math is closed-form over small fixed float vectors.

Usage:
  python harnesses/ai/drift_detection_test_harness.py --self-test
  python harnesses/ai/drift_detection_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
import contextlib

from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

with contextlib.suppress(Exception):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

EPS = 1e-6

# Alert thresholds (mirrored in the paired unittest).
PSI_ALERT = 0.25
KL_ALERT = 0.20
JS_ALERT = 0.20
HELLINGER_ALERT = 0.30
CENTROID_ALERT = 0.50
COSINE_DROP_ALERT = 0.10
SPEARMAN_ALERT = 0.70
CHURN_ALERT = 0.20


# ---------------------------------------------------------------------------
# Hand-rolled metrics (pure stdlib over list[float])
# ---------------------------------------------------------------------------


def psi(base: tuple[float, ...], cur: tuple[float, ...]) -> float:
    """Population Stability Index with an epsilon floor on empty bins."""
    s = 0.0
    for b, c in zip(base, cur, strict=False):
        bb, cc = max(b, EPS), max(c, EPS)
        s += (cc - bb) * math.log(cc / bb)
    return s


def psi_zero_floor(base: tuple[float, ...], cur: tuple[float, ...]) -> float:
    """Buggy: no epsilon floor — skips empty bins, dropping their dominant terms."""
    s = 0.0
    for b, c in zip(base, cur, strict=False):
        if b == 0 or c == 0:
            continue
        s += (c - b) * math.log(c / b)
    return s


def kl_div(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    """KL(p || q) with q floored; 0*log0 contributes 0 per convention."""
    s = 0.0
    for pi, qi in zip(p, q, strict=False):
        if pi <= 0:
            continue
        s += pi * math.log(pi / max(qi, EPS))
    return s


def js_div(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    """Jensen-Shannon divergence (symmetric, bounded [0, ln2])."""
    m = tuple((pi + qi) / 2 for pi, qi in zip(p, q, strict=False))
    return 0.5 * kl_div(p, m) + 0.5 * kl_div(q, m)


def hellinger(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    s = sum((math.sqrt(pi) - math.sqrt(qi)) ** 2 for pi, qi in zip(p, q, strict=False))
    return math.sqrt(s / 2)


def _mean_vec(vecs: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
    n = len(vecs)
    dims = len(vecs[0])
    return tuple(sum(v[d] for v in vecs) / n for d in range(dims))


def _euclid(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return math.sqrt(sum((ai - bi) ** 2 for ai, bi in zip(a, b, strict=False)))


def centroid_distance(base: tuple[tuple[float, ...], ...],
                      cur: tuple[tuple[float, ...], ...]) -> float:
    return _euclid(_mean_vec(base), _mean_vec(cur))


def centroid_averaged(base: tuple[tuple[float, ...], ...],
                      cur: tuple[tuple[float, ...], ...]) -> float:
    """Buggy: divides the displacement by dimensionality, washing out the shift."""
    cb, cc = _mean_vec(base), _mean_vec(cur)
    return _euclid(cb, cc) / len(cb)


def _dot(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(ai * bi for ai, bi in zip(a, b, strict=False))


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    na, nb = math.sqrt(_dot(a, a)), math.sqrt(_dot(b, b))
    if na == 0 or nb == 0:
        return 0.0
    return _dot(a, b) / (na * nb)


def cosine_mean_drop(query: tuple[float, ...], base_docs: tuple[tuple[float, ...], ...],
                     cur_docs: tuple[tuple[float, ...], ...]) -> float:
    mb = sum(_cosine(query, d) for d in base_docs) / len(base_docs)
    mc = sum(_cosine(query, d) for d in cur_docs) / len(cur_docs)
    return (mb - mc) / mb if mb else 0.0


def cosine_unnormalized_drop(query: tuple[float, ...],
                             base_docs: tuple[tuple[float, ...], ...],
                             cur_docs: tuple[tuple[float, ...], ...]) -> float:
    """Buggy: uses the raw dot product instead of the normalized cosine."""
    mb = sum(_dot(query, d) for d in base_docs) / len(base_docs)
    mc = sum(_dot(query, d) for d in cur_docs) / len(cur_docs)
    return (mb - mc) / mb if mb else 0.0


def spearman(base: tuple[str, ...], cur: tuple[str, ...]) -> float:
    """Spearman rank correlation over the common item set."""
    bpos = {it: i for i, it in enumerate(base)}
    cpos = {it: i for i, it in enumerate(cur)}
    common = [it for it in base if it in cpos]
    n = len(common)
    if n < 2:
        return 1.0
    d2 = sum((bpos[it] - cpos[it]) ** 2 for it in common)
    return 1 - 6 * d2 / (n * (n * n - 1))


def rank_overlap_only(base: tuple[str, ...], cur: tuple[str, ...]) -> float:
    """Buggy: reports set overlap as if it were a rank correlation."""
    return len(set(base) & set(cur)) / len(base) if base else 1.0


def neighborhood_churn(base: tuple[str, ...], cur: tuple[str, ...]) -> float:
    return 1 - (len(set(base) & set(cur)) / len(base) if base else 1.0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftCase:
    base_dist: tuple[float, ...]
    cur_dist: tuple[float, ...]
    base_emb: tuple[tuple[float, ...], ...]
    cur_emb: tuple[tuple[float, ...], ...]
    cos_query: tuple[float, ...]
    cos_base_docs: tuple[tuple[float, ...], ...]
    cos_cur_docs: tuple[tuple[float, ...], ...]
    rank_base: tuple[str, ...]
    rank_cur: tuple[str, ...]
    churn_base: tuple[str, ...]
    churn_cur: tuple[str, ...]
    version_match: bool


DRIFT_BASE_DIST = (0.45, 0.30, 0.15, 0.07, 0.03)
DRIFT_CUR_DIST = (0.03, 0.07, 0.15, 0.30, 0.45)
STABLE_BASE_DIST = (0.45, 0.30, 0.15, 0.07, 0.03)
STABLE_CUR_DIST = (0.44, 0.30, 0.16, 0.07, 0.03)

# dedicated pairs isolating one buggy detector each
PSI_ZB_BASE = (0.22, 0.22, 0.20, 0.20, 0.10, 0.06)
PSI_ZB_CUR = (0.255, 0.255, 0.23, 0.23, 0.03, 0.00)   # one empty bin
KL_SWAP_BASE = (0.40, 0.40, 0.10, 0.10)
KL_SWAP_CUR = (0.40, 0.40, 0.195, 0.005)              # forward KL >0.2, reverse <0.2

EMB_BASE = (
    (0.1, 0.0, 0.0, 0.0), (0.0, 0.1, 0.0, 0.0), (0.0, 0.0, 0.1, 0.0),
    (0.0, 0.0, 0.0, 0.1), (0.0, 0.0, 0.0, 0.0),
)
EMB_CUR = (
    (0.7, 0.6, 0.6, 0.6), (0.6, 0.7, 0.6, 0.6), (0.6, 0.6, 0.7, 0.6),
    (0.6, 0.6, 0.6, 0.7), (0.6, 0.6, 0.6, 0.6),
)
EMB_BASE_JITTER = (
    (0.1, 0.0, 0.0, 0.0), (0.0, 0.1, 0.0, 0.0), (0.0, 0.0, 0.1, 0.0),
    (0.0, 0.0, 0.0, 0.1), (0.0, 0.0, 0.0, 0.01),
)

COS_QUERY = (1.0, 0.0, 0.0, 0.0)
COS_BASE_DOCS = (
    (0.9, 0.1, 0.0, 0.0), (0.95, 0.05, 0.0, 0.0),
    (0.85, 0.1, 0.05, 0.0), (0.9, 0.0, 0.1, 0.0),
)
COS_CUR_DOCS = (   # low cosine with the query, but large raw dot product
    (1.0, 3.0, 1.0, 1.0), (1.0, 2.5, 1.0, 1.0),
    (0.9, 3.0, 0.9, 0.9), (1.0, 3.0, 1.0, 0.8),
)

RANK_BASE = ("d1", "d2", "d3", "d4", "d5")
RANK_REVERSED = ("d5", "d4", "d3", "d2", "d1")   # same set, reordered
CHURN_BASE = ("d1", "d2", "d3", "d4", "d5")
CHURN_CHANGED = ("d1", "d2", "x6", "x7", "x8")   # set changes ~60%


DRIFTED_CASE = DriftCase(
    DRIFT_BASE_DIST, DRIFT_CUR_DIST, EMB_BASE, EMB_CUR,
    COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS,
    RANK_BASE, RANK_REVERSED, CHURN_BASE, CHURN_CHANGED, version_match=True)

STABLE_CASE = DriftCase(
    STABLE_BASE_DIST, STABLE_CUR_DIST, EMB_BASE, EMB_BASE_JITTER,
    COS_QUERY, COS_BASE_DOCS, COS_BASE_DOCS,
    RANK_BASE, RANK_BASE, CHURN_BASE, CHURN_BASE, version_match=True)

VERSION_MISMATCH_CASE = DriftCase(
    STABLE_BASE_DIST, STABLE_CUR_DIST, EMB_BASE, EMB_BASE_JITTER,
    COS_QUERY, COS_BASE_DOCS, COS_BASE_DOCS,
    RANK_BASE, RANK_BASE, CHURN_BASE, CHURN_BASE, version_match=False)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


@dataclass
class DriftReport:
    psi: float
    kl: float
    js: float
    hellinger: float
    centroid_dist: float
    cosine_mean_drop: float
    spearman_tau: float
    neighborhood_churn: float
    version_mismatch: bool

    @property
    def psi_alert(self) -> bool:
        return self.psi > PSI_ALERT

    @property
    def kl_alert(self) -> bool:
        return self.kl > KL_ALERT

    @property
    def js_alert(self) -> bool:
        return self.js > JS_ALERT

    @property
    def hellinger_alert(self) -> bool:
        return self.hellinger > HELLINGER_ALERT

    @property
    def centroid_alert(self) -> bool:
        return self.centroid_dist > CENTROID_ALERT

    @property
    def cosine_alert(self) -> bool:
        return self.cosine_mean_drop > COSINE_DROP_ALERT

    @property
    def rank_alert(self) -> bool:
        return self.spearman_tau < SPEARMAN_ALERT

    @property
    def churn_alert(self) -> bool:
        return self.neighborhood_churn > CHURN_ALERT

    def any_drift(self) -> bool:
        return (self.psi_alert or self.kl_alert or self.js_alert or self.hellinger_alert
                or self.centroid_alert or self.cosine_alert or self.rank_alert
                or self.churn_alert or self.version_mismatch)

    def is_stable(self) -> bool:
        return not self.any_drift()


PsiFn = Callable[[tuple, tuple], float]
CentroidFn = Callable[[tuple, tuple], float]
CosineFn = Callable[[tuple, tuple, tuple], float]
RankFn = Callable[[tuple, tuple], float]


def compute_drift(case: DriftCase, *, psi_fn: PsiFn = psi, kl_fn: PsiFn = kl_div,
                  centroid_fn: CentroidFn = centroid_distance,
                  cosine_fn: CosineFn = cosine_mean_drop, rank_fn: RankFn = spearman,
                  churn_fn: RankFn = neighborhood_churn,
                  version_aware: bool = True) -> DriftReport:
    return DriftReport(
        psi=psi_fn(case.base_dist, case.cur_dist),
        kl=kl_fn(case.base_dist, case.cur_dist),
        js=js_div(case.base_dist, case.cur_dist),
        hellinger=hellinger(case.base_dist, case.cur_dist),
        centroid_dist=centroid_fn(case.base_emb, case.cur_emb),
        cosine_mean_drop=cosine_fn(case.cos_query, case.cos_base_docs, case.cos_cur_docs),
        spearman_tau=rank_fn(case.rank_base, case.rank_cur),
        neighborhood_churn=churn_fn(case.churn_base, case.churn_cur),
        version_mismatch=(not case.version_match) if version_aware else False,
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


def _chk(name: str, cond: bool, detail: str = "") -> Check:
    return Check(name, bool(cond), detail)


def s_oracle_detects_planted_drift() -> Check:
    rep = compute_drift(DRIFTED_CASE)
    return _chk("oracle_detects_planted_drift", rep.any_drift(),
                f"psi={rep.psi:.2f} kl={rep.kl:.2f} js={rep.js:.2f} "
                f"hell={rep.hellinger:.2f} cen={rep.centroid_dist:.2f} "
                f"cos={rep.cosine_mean_drop:.2f} tau={rep.spearman_tau:.2f} "
                f"churn={rep.neighborhood_churn:.2f}")


def s_oracle_no_false_alarm_on_stable() -> Check:
    rep = compute_drift(STABLE_CASE)
    return _chk("oracle_no_false_alarm_on_stable", rep.is_stable(),
                f"psi={rep.psi:.3f} kl={rep.kl:.3f} js={rep.js:.3f}")


def s_psi_above_025_alerts() -> Check:
    v = psi(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("psi_above_025_alerts", v > PSI_ALERT, f"psi={v:.3f}")


def s_psi_stable_below_threshold() -> Check:
    v = psi(STABLE_BASE_DIST, STABLE_CUR_DIST)
    return _chk("psi_stable_below_threshold", v < PSI_ALERT, f"psi={v:.4f}")


def s_psi_zero_floor_caught() -> Check:
    o = psi(PSI_ZB_BASE, PSI_ZB_CUR)
    b = psi_zero_floor(PSI_ZB_BASE, PSI_ZB_CUR)
    return _chk("psi_zero_floor_caught", o > PSI_ALERT and b < PSI_ALERT,
                f"oracle={o:.3f} buggy={b:.3f}")


def s_kl_above_02_alerts() -> Check:
    v = kl_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("kl_above_02_alerts", v > KL_ALERT, f"kl={v:.3f}")


def s_kl_symmetric_swap_caught() -> Check:
    forward = kl_div(KL_SWAP_BASE, KL_SWAP_CUR)
    reverse = kl_div(KL_SWAP_CUR, KL_SWAP_BASE)
    return _chk("kl_symmetric_swap_caught", forward > KL_ALERT and reverse < KL_ALERT,
                f"forward={forward:.3f} swapped={reverse:.3f}")


def s_js_above_02_alerts() -> Check:
    v = js_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("js_above_02_alerts", v > JS_ALERT, f"js={v:.3f}")


def s_js_symmetry_property() -> Check:
    a = js_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    b = js_div(DRIFT_CUR_DIST, DRIFT_BASE_DIST)
    return _chk("js_symmetry_property", abs(a - b) < 1e-9, f"{a:.4f} vs {b:.4f}")


def s_js_bounded_0_1() -> Check:
    v = js_div(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("js_bounded_0_1", 0.0 <= v <= 1.0, f"js={v:.3f}")


def s_hellinger_above_03_alerts() -> Check:
    v = hellinger(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("hellinger_above_03_alerts", v > HELLINGER_ALERT, f"h={v:.3f}")


def s_hellinger_bounded_0_1() -> Check:
    v = hellinger(DRIFT_BASE_DIST, DRIFT_CUR_DIST)
    return _chk("hellinger_bounded_0_1", 0.0 <= v <= 1.0, f"h={v:.3f}")


def s_centroid_euclidean_displacement() -> Check:
    v = centroid_distance(EMB_BASE, EMB_CUR)
    return _chk("centroid_euclidean_displacement", v > CENTROID_ALERT, f"dist={v:.3f}")


def s_centroid_averaged_caught() -> Check:
    o = centroid_distance(EMB_BASE, EMB_CUR)
    b = centroid_averaged(EMB_BASE, EMB_CUR)
    return _chk("centroid_averaged_caught", o > CENTROID_ALERT and b < CENTROID_ALERT,
                f"oracle={o:.3f} buggy={b:.3f}")


def s_cosine_mean_drop_over_10pct() -> Check:
    v = cosine_mean_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS)
    return _chk("cosine_mean_drop_over_10pct", v > COSINE_DROP_ALERT, f"drop={v:.3f}")


def s_cosine_unnormalized_caught() -> Check:
    o = cosine_mean_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS)
    b = cosine_unnormalized_drop(COS_QUERY, COS_BASE_DOCS, COS_CUR_DOCS)
    return _chk("cosine_unnormalized_caught", o > COSINE_DROP_ALERT and b < COSINE_DROP_ALERT,
                f"oracle={o:.3f} buggy={b:.3f}")


def s_spearman_below_07_alerts() -> Check:
    v = spearman(RANK_BASE, RANK_REVERSED)
    return _chk("spearman_below_07_alerts", v < SPEARMAN_ALERT, f"tau={v:.3f}")


def s_rank_overlap_only_caught() -> Check:
    o = spearman(RANK_BASE, RANK_REVERSED)
    b = rank_overlap_only(RANK_BASE, RANK_REVERSED)
    return _chk("rank_overlap_only_caught", o < SPEARMAN_ALERT and b >= SPEARMAN_ALERT,
                f"oracle_tau={o:.2f} buggy_overlap={b:.2f}")


def s_neighborhood_churn_over_20pct() -> Check:
    v = neighborhood_churn(CHURN_BASE, CHURN_CHANGED)
    return _chk("neighborhood_churn_over_20pct", v > CHURN_ALERT, f"churn={v:.2f}")


def s_version_mismatch_flags_drift() -> Check:
    rep = compute_drift(VERSION_MISMATCH_CASE)
    return _chk("version_mismatch_flags_drift", rep.version_mismatch and rep.any_drift())


def s_version_blind_caught() -> Check:
    o = compute_drift(VERSION_MISMATCH_CASE)
    b = compute_drift(VERSION_MISMATCH_CASE, version_aware=False)
    return _chk("version_blind_caught",
                o.version_mismatch and o.any_drift()
                and not b.version_mismatch and b.is_stable(),
                f"oracle_flag={o.version_mismatch} buggy_flag={b.version_mismatch}")


def s_false_alarm_detector_caught() -> Check:
    o = compute_drift(STABLE_CASE)
    b = compute_drift(STABLE_CASE, psi_fn=lambda base, cur: 0.5)
    return _chk("false_alarm_detector_caught", o.is_stable() and b.any_drift(),
                f"oracle_stable={o.is_stable()} buggy_drift={b.any_drift()}")


def s_stable_case_all_metrics_below() -> Check:
    r = compute_drift(STABLE_CASE)
    flags = (r.psi_alert, r.kl_alert, r.js_alert, r.hellinger_alert, r.centroid_alert,
             r.cosine_alert, r.rank_alert, r.churn_alert, r.version_mismatch)
    return _chk("stable_case_all_metrics_below", not any(flags), f"flags={flags}")


def s_drift_report_alert_flags_wired() -> Check:
    r = compute_drift(DRIFTED_CASE)
    return _chk("drift_report_alert_flags_wired",
                r.psi_alert and r.kl_alert and r.js_alert and r.hellinger_alert
                and r.centroid_alert and r.cosine_alert and r.rank_alert and r.churn_alert)


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (reference dist, live dist) -> expected drift verdict
#
# A drift detector only has teeth if it CATCHES a detector that is asleep at the
# wheel — one that NEVER fires when the distribution has genuinely shifted, or one
# that cries wolf on a distribution that has NOT moved. The contract every correct
# PSI-based detector must hold:
#
#   * it ALERTS (verdict True) when the Population Stability Index exceeds the
#     industry-standard PSI_ALERT = 0.25 threshold;
#   * it stays SILENT (verdict False) on identical or jittered distributions whose
#     PSI is below 0.25.
#
# An impl is a callable ``detector(base, cur) -> bool`` returning True iff it flags
# drift. prove() judges each impl against the corpus's FROZEN LITERAL verdicts
# (the True/False drift decisions below, hand-computed by evaluating PSI against
# the 0.25 threshold OFFLINE and baked in as constants — NEVER re-derived from
# psi() at runtime), so the check is non-circular. prove(impl) is True iff the
# detector's verdict diverges from the frozen literal on ANY case — i.e. a planted
# detector bug is caught.
#
# Pure + deterministic: closed-form float math over fixed vectors, no RNG, no
# clock, no network, no filesystem, no threads. The three planted mutants model
# genuine real-world drift-monitoring failures (per the campaign hint):
#
#   * never_fires_threshold — the alert threshold is set absurdly high (a common
#     mis-tuning), so PSI never crosses it and real drift sails through undetected;
#   * fires_on_identical — alerts on ANY non-degenerate comparison (threshold <= 0),
#     so the monitor cries wolf even when the live distribution is identical to the
#     reference (alert fatigue / the boy-who-cried-wolf failure);
#   * psi_no_epsilon_floor — uses the PSI variant that skips empty bins instead of
#     flooring them with epsilon, so a distribution that drains a bin to zero (a
#     real, dangerous shift) is silently under-counted and missed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftVerdictCase:
    """One frozen (reference, live) distribution pair with a literal verdict."""
    name: str
    base_dist: tuple[float, ...]
    cur_dist: tuple[float, ...]
    expected_drift: bool   # the EXACT verdict a correct PSI@0.25 detector yields
    note: str = ""


# Verdicts are hand-computed by evaluating PSI (epsilon-floored) against the 0.25
# alert threshold OFFLINE, then baked in as the True/False literals below. They are
# constants — prove() never calls psi() to re-derive them, so the gate cannot be
# fooled by a circular oracle comparison. (Reference PSI values, for the record:
# reverse_drift~2.94, big_shift~2.46, empty_bin~0.76 => drift; stable_jitter~0.0009,
# identical=0.0, mild_shift~0.016 => no drift.)
DRIFT_VERDICT_CORPUS: tuple[DriftVerdictCase, ...] = (
    DriftVerdictCase(
        "reverse_drift",
        (0.45, 0.30, 0.15, 0.07, 0.03), (0.03, 0.07, 0.15, 0.30, 0.45),
        True, "mass fully reversed across bins -> PSI ~2.94, far above 0.25"),
    DriftVerdictCase(
        "big_shift",
        (0.70, 0.20, 0.07, 0.03), (0.10, 0.20, 0.30, 0.40),
        True, "dominant bin collapses, tail inflates -> PSI ~2.46, drift"),
    DriftVerdictCase(
        "empty_bin",
        (0.22, 0.22, 0.20, 0.20, 0.10, 0.06), (0.255, 0.255, 0.23, 0.23, 0.03, 0.00),
        True, "one bin drains to zero -> PSI ~0.76 WITH the epsilon floor; a "
              "no-floor variant skips that bin and misses the drift"),
    DriftVerdictCase(
        "stable_jitter",
        (0.45, 0.30, 0.15, 0.07, 0.03), (0.44, 0.30, 0.16, 0.07, 0.03),
        False, "sub-percent measurement jitter -> PSI ~0.0009, no real drift"),
    DriftVerdictCase(
        "identical",
        (0.25, 0.25, 0.25, 0.25), (0.25, 0.25, 0.25, 0.25),
        False, "live distribution identical to reference -> PSI 0.0, no drift"),
    DriftVerdictCase(
        "mild_shift",
        (0.40, 0.30, 0.20, 0.10), (0.35, 0.30, 0.22, 0.13),
        False, "small benign reweighting -> PSI ~0.016, below the 0.25 alert"),
)


# --- ORACLE: reuse the harness's own correct epsilon-floored PSI + 0.25 threshold

def oracle_drift_detector(base: tuple[float, ...], cur: tuple[float, ...]) -> bool:
    """Correct drift verdict: the harness's own epsilon-floored ``psi`` compared
    against the industry-standard ``PSI_ALERT`` (0.25) threshold."""
    return psi(base, cur) > PSI_ALERT


# --- Planted buggy twins (each models a real drift-monitoring failure) --------

def never_fires_threshold(base: tuple[float, ...], cur: tuple[float, ...]) -> bool:
    """BUG: the alert threshold is mis-tuned absurdly high (10.0), so even a fully
    reversed distribution (PSI ~2.94) never crosses it. Real drift goes unflagged —
    the most common "our monitor never alerts" production failure."""
    return psi(base, cur) > 10.0


def fires_on_identical(base: tuple[float, ...], cur: tuple[float, ...]) -> bool:
    """BUG: alerts whenever PSI is >= 0 (always, since PSI is non-negative), so the
    monitor flags drift even when the live distribution is byte-identical to the
    reference. The boy-who-cried-wolf failure -> alert fatigue, ignored alarms."""
    return psi(base, cur) >= 0.0


def psi_no_epsilon_floor(base: tuple[float, ...], cur: tuple[float, ...]) -> bool:
    """BUG: uses the no-epsilon-floor PSI variant (``psi_zero_floor``) that simply
    skips bins where either side is zero. When a bin drains to zero — a real,
    dangerous shift — its dominant divergence term is dropped, so PSI is
    under-counted and the drift slips below the 0.25 alert and is missed."""
    return psi_zero_floor(base, cur) > PSI_ALERT


def prove(impl: Callable[[tuple, tuple], bool]) -> bool:
    """True iff ``impl`` returns the WRONG drift verdict on ANY frozen corpus case
    (i.e. the detector bug is caught): it stays silent where the frozen literal says
    drift, or alerts where the literal says no-drift.

    Non-circular + deterministic: every verdict is a literal baked into
    DRIFT_VERDICT_CORPUS, never read back from ``psi``/the oracle at runtime; the
    math is closed-form over fixed float vectors with no RNG/clock/network/
    filesystem. An impl that raises on a corpus case counts as caught.
    """
    for case in DRIFT_VERDICT_CORPUS:
        try:
            verdict = impl(case.base_dist, case.cur_dist)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if bool(verdict) != case.expected_drift:
            return True
    return False


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_drift_detector"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_drift_detector,
    mutants=(
        Mutant("never_fires_threshold", never_fires_threshold,
               "alert threshold mis-tuned to 10.0 -> PSI never crosses it and "
               "genuine distribution drift is never flagged"),
        Mutant("fires_on_identical", fires_on_identical,
               "alerts whenever PSI >= 0 (always) -> cries wolf even on an "
               "identical distribution, causing alert fatigue"),
        Mutant("psi_no_epsilon_floor", psi_no_epsilon_floor,
               "drops empty bins instead of flooring them with epsilon -> a bin "
               "that drains to zero is under-counted and the drift is missed"),
    ),
    corpus_size=len(DRIFT_VERDICT_CORPUS),
    kind="statistical",
    notes="a drift detector must alert iff PSI exceeds the 0.25 threshold: fire on "
          "a genuinely shifted distribution, stay silent on an identical/jittered "
          "one. Verdicts are frozen literals, never re-derived from psi() at runtime.",
)


SCENARIOS: dict[str, Callable[[], Check]] = {
    f.__name__[2:]: f
    for f in [
        s_oracle_detects_planted_drift,
        s_oracle_no_false_alarm_on_stable,
        s_psi_above_025_alerts,
        s_psi_stable_below_threshold,
        s_psi_zero_floor_caught,
        s_kl_above_02_alerts,
        s_kl_symmetric_swap_caught,
        s_js_above_02_alerts,
        s_js_symmetry_property,
        s_js_bounded_0_1,
        s_hellinger_above_03_alerts,
        s_hellinger_bounded_0_1,
        s_centroid_euclidean_displacement,
        s_centroid_averaged_caught,
        s_cosine_mean_drop_over_10pct,
        s_cosine_unnormalized_caught,
        s_spearman_below_07_alerts,
        s_rank_overlap_only_caught,
        s_neighborhood_churn_over_20pct,
        s_version_mismatch_flags_drift,
        s_version_blind_caught,
        s_false_alarm_detector_caught,
        s_stable_case_all_metrics_below,
        s_drift_report_alert_flags_wired,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    """Run every drift scenario, then assert the teeth: the correct PSI detector is
    not flagged and every planted asleep-/trigger-happy detector is caught."""
    report = Report("ai/drift_detection")

    # 1. The harness's existing meaningful scenario checks (drift fires, stable
    #    stays silent, each buggy detector is individually caught, etc.).
    for fn in SCENARIOS.values():
        chk = fn()
        report.record(chk.name, chk.passed, detail=chk.detail)

    # 2. Teeth: prove(oracle) is False AND every planted mutant is caught, judged
    #    against the FROZEN literal drift verdicts (non-circular).
    for case in DRIFT_VERDICT_CORPUS:
        report.add(f"oracle_verdict:{case.name}", case.expected_drift,
                   oracle_drift_detector(case.base_dist, case.cur_dist),
                   detail=case.note)
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Model/embedding drift detection harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    if args.self_test or args.json:
        return _run_self_test(verbose=args.verbose, as_json=args.json)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
