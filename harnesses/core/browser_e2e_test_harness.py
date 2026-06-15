#!/usr/bin/env python3
"""
browser_e2e_test_harness.py — Deterministic DOM/E2E surrogate: staleness, settling, mocks.
===========================================================================================

Pure-stdlib. Zero external dependencies. No real browser, no asyncio.

End-to-end tests flake on things a unit test never sees: a cached element handle
goes stale after a re-render and the click lands on a detached node; an assertion
runs before the async work settles; events fire out of order (click before change,
input before focus); an unmocked request returns a silent 404 instead of failing
loudly; server-rendered markup and the client render structurally disagree
(hydration mismatch); a brittle absolute XPath breaks when the tree shifts while a
role/testid selector still resolves. This harness models the DOM as immutable data,
re-render as a pure tree mutation, and async work as a manually-drained FIFO — so
every check is deterministic. Six buggy implementations each reproduce one flake.

In-process, no socket, no real event loop.

Usage:
  python harnesses/core/browser_e2e_test_harness.py --self-test
  python harnesses/core/browser_e2e_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from dataclasses import dataclass

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Node:
    node_id: str
    tag: str
    role: str = ""
    label: str = ""
    testid: str = ""
    text: str = ""
    focusable: bool = False
    children: tuple[str, ...] = ()


@dataclass
class Dom:
    nodes: dict[str, Node]
    root: str


@dataclass(frozen=True)
class Selector:
    kind: str   # role | label | testid | xpath
    value: str


@dataclass(frozen=True)
class MockResponse:
    status: int
    body: str


@dataclass(frozen=True)
class EventRecord:
    kind: str
    node_id: str
    seq: int


class UnmockedRequestError(Exception):
    """Raised when a request targets a URL with no registered mock."""


class PrematureAssertionError(Exception):
    """Raised when an assertion runs while async work is still pending."""


# ---------------------------------------------------------------------------
# Deterministic event loop (manually drained; no real async)
# ---------------------------------------------------------------------------


class EventLoop:
    def __init__(self) -> None:
        self.pending: list[Callable[[], None]] = []

    def schedule(self, fn: Callable[[], None]) -> None:
        self.pending.append(fn)

    def tick(self) -> None:
        if self.pending:
            self.pending.pop(0)()

    def settle(self) -> None:
        while self.pending:
            self.pending.pop(0)()


def assert_settled(loop: EventLoop) -> None:
    if loop.pending:
        raise PrematureAssertionError(f"{len(loop.pending)} pending op(s)")


def navigate(loop: EventLoop, url: str) -> None:
    """Navigation enqueues async work that must settle before assertions."""
    loop.schedule(lambda: None)


# ---------------------------------------------------------------------------
# Fixtures — an initial DOM, a pure re-render mutation, routes, server render
# ---------------------------------------------------------------------------


def _build_dom() -> Dom:
    nodes = {
        "root": Node("root", "body", children=("container", "footer")),
        "container": Node("container", "div", children=("heading", "form")),
        "heading": Node("heading", "h1", text="Login"),
        "form": Node("form", "form", role="form", children=("email", "submit", "status")),
        "email": Node("email", "input", role="textbox", label="Email",
                      testid="email-input", focusable=True),
        "submit": Node("submit", "button", role="button", label="Submit",
                       testid="submit-btn", focusable=True),
        "status": Node("status", "div", role="status", text="ready"),
        "footer": Node("footer", "div", text="(c)"),
    }
    return Dom(nodes, "root")


INITIAL_DOM = _build_dom()
SUBMIT_SELECTOR = Selector("testid", "submit-btn")
# absolute path to submit in the INITIAL tree: root>container[0]>form[1]>submit[1]
SUBMIT_XPATH_INITIAL = "/0/1/1"

ROUTES: dict[str, MockResponse] = {
    "/api/user": MockResponse(200, "{}"),
    "/api/orders": MockResponse(200, "[]"),
    "/api/login": MockResponse(200, "ok"),
    "/health": MockResponse(200, "ok"),
    "/api/logout": MockResponse(200, ""),
}
UNMOCKED_URL = "/api/secret"


def apply_mutation(dom: Dom) -> Dom:
    """Pure re-render: re-key the submit button (new node id, same role/testid),
    prepend a spinner to the form — shifting child indices and detaching old handles."""
    nodes = dict(dom.nodes)
    del nodes["submit"]
    nodes["submit_v2"] = Node("submit_v2", "button", role="button", label="Submit",
                              testid="submit-btn", focusable=True)
    nodes["spinner"] = Node("spinner", "div", role="progressbar", text="loading")
    form = nodes["form"]
    nodes["form"] = Node(form.node_id, form.tag, form.role, form.label, form.testid,
                         form.text, form.focusable, ("spinner", "email", "submit_v2", "status"))
    return Dom(nodes, dom.root)


MUTATED_DOM = apply_mutation(INITIAL_DOM)


# ---------------------------------------------------------------------------
# Selector resolution
# ---------------------------------------------------------------------------


def resolve(selector: Selector, dom: Dom) -> str | None:
    if selector.kind == "xpath":
        return _resolve_xpath(dom, selector.value)
    attr = {"role": "role", "label": "label", "testid": "testid"}[selector.kind]
    for nid, n in dom.nodes.items():
        if getattr(n, attr) == selector.value:
            return nid
    return None


def _resolve_xpath(dom: Dom, path: str) -> str | None:
    parts = [int(p) for p in path.strip("/").split("/") if p != ""]
    cur = dom.root
    for idx in parts:
        node = dom.nodes.get(cur)
        if node is None or idx >= len(node.children):
            return None
        cur = node.children[idx]
    return cur


# ---------------------------------------------------------------------------
# Implementations (oracle + buggy) for each dimension
# ---------------------------------------------------------------------------


def robust_clicker(initial: Dom, mutated: Dom, selector: Selector) -> bool:
    """Re-resolve the selector against the current DOM before clicking."""
    nid = resolve(selector, mutated)
    return nid is not None and nid in mutated.nodes


def stale_clicker(initial: Dom, mutated: Dom, selector: Selector) -> bool:
    """Buggy: clicks a node id cached from the pre-render DOM."""
    nid = resolve(selector, initial)
    return nid is not None and nid in mutated.nodes


def oracle_settle(loop: EventLoop) -> None:
    loop.settle()


def no_settle(loop: EventLoop) -> None:
    """Buggy: asserts without draining pending async work."""
    return None


def event_emitter_oracle() -> list[EventRecord]:
    return [EventRecord("focus", "email", 0), EventRecord("input", "email", 1),
            EventRecord("change", "email", 2), EventRecord("click", "submit", 3)]


def event_emitter_reordered() -> list[EventRecord]:
    """Buggy: emits click before change."""
    return [EventRecord("focus", "email", 0), EventRecord("input", "email", 1),
            EventRecord("click", "submit", 2), EventRecord("change", "email", 3)]


def count_event_order_violations(events: list[EventRecord]) -> int:
    seq = {e.kind: e.seq for e in events}
    v = 0
    if "change" in seq and "click" in seq and not seq["change"] < seq["click"]:
        v += 1
    if "focus" in seq and "input" in seq and not seq["focus"] < seq["input"]:
        v += 1
    return v


def oracle_fetch(url: str, routes: dict[str, MockResponse]) -> MockResponse:
    if url in routes:
        return routes[url]
    raise UnmockedRequestError(url)


def silent_404_fetch(url: str, routes: dict[str, MockResponse]) -> MockResponse:
    """Buggy: returns a silent 404 for an unmocked URL instead of raising."""
    if url in routes:
        return routes[url]
    return MockResponse(404, "")


def faithful_render(server: Dom) -> Dom:
    return server


def hydration_blind_render(server: Dom) -> Dom:
    """Buggy: client render drops the heading — structural mismatch vs server."""
    nodes = dict(server.nodes)
    cont = nodes["container"]
    nodes["container"] = Node(cont.node_id, cont.tag, cont.role, cont.label,
                              cont.testid, cont.text, cont.focusable, ("form",))
    nodes.pop("heading", None)
    return Dom(nodes, server.root)


def _preorder(dom: Dom) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []

    def walk(nid: str) -> None:
        n = dom.nodes.get(nid)
        if n is None:
            return
        out.append((n.tag, n.role))
        for c in n.children:
            walk(c)

    walk(dom.root)
    return out


def hydration_diff(a: Dom, b: Dom) -> int:
    pa, pb = _preorder(a), _preorder(b)
    diff = abs(len(pa) - len(pb))
    for x, y in zip(pa, pb):
        if x != y:
            diff += 1
    return diff


def oracle_selector(dom: Dom) -> str | None:
    return resolve(Selector("testid", "submit-btn"), dom)


def brittle_xpath_selector(dom: Dom) -> str | None:
    """Buggy: absolute XPath valid only against the pre-render tree."""
    return resolve(Selector("xpath", SUBMIT_XPATH_INITIAL), dom)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


@dataclass
class E2EReport:
    stale_clicks: int
    premature_assertions: int
    event_order_violations: int
    unmocked_silent: int
    hydration_mismatches: int
    selector_breaks: int

    @property
    def total_failures(self) -> int:
        return (self.stale_clicks + self.premature_assertions + self.event_order_violations
                + self.unmocked_silent + self.hydration_mismatches + self.selector_breaks)

    def meets_floors(self) -> bool:
        return self.total_failures == 0


def _check_premature(strategy: Callable[[EventLoop], None]) -> int:
    loop = EventLoop()
    loop.schedule(lambda: None)
    strategy(loop)
    return 1 if loop.pending else 0


def _check_unmocked(fetch_impl: Callable[[str, dict], MockResponse]) -> int:
    try:
        fetch_impl(UNMOCKED_URL, ROUTES)
        return 1  # returned silently instead of raising
    except UnmockedRequestError:
        return 0


def _check_selector(strategy: Callable[[Dom], str | None]) -> int:
    nid = strategy(MUTATED_DOM)
    node = MUTATED_DOM.nodes.get(nid) if nid else None
    return 0 if (node is not None and node.testid == "submit-btn") else 1


def audit(*, clicker=robust_clicker, settle_strategy=oracle_settle,
          emitter=event_emitter_oracle, fetch_impl=oracle_fetch,
          renderer=faithful_render, selector=oracle_selector) -> E2EReport:
    return E2EReport(
        stale_clicks=0 if clicker(INITIAL_DOM, MUTATED_DOM, SUBMIT_SELECTOR) else 1,
        premature_assertions=_check_premature(settle_strategy),
        event_order_violations=count_event_order_violations(emitter()),
        unmocked_silent=_check_unmocked(fetch_impl),
        hydration_mismatches=hydration_diff(INITIAL_DOM, renderer(INITIAL_DOM)),
        selector_breaks=_check_selector(selector),
    )


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of (E2E scenario config -> the exact failure-count
# vector a correct auditor MUST report).
#
# A browser/E2E harness only has teeth if its AUDITOR catches each flake class
# (stale handle, premature assertion, out-of-order events, silent-404, hydration
# mismatch, brittle selector) — and, crucially, does NOT cry wolf on a clean run.
# The thing under test is therefore an *auditor*: a callable
#
#   auditor(initial, mutated, *, clicker, settle_strategy, emitter, fetch_impl,
#           renderer, selector) -> Tuple[int, int, int, int, int, int]
#
# returning the six failure counts in this fixed order:
#   (stale_clicks, premature_assertions, event_order_violations,
#    unmocked_silent, hydration_mismatches, selector_breaks).
#
# Each corpus case pins one configuration of the six pluggable strategies to the
# EXACT failure vector a correct auditor must emit. Those vectors are FROZEN
# LITERALS, hand-derived from the per-dimension contract below — they are NEVER
# read back from the oracle at runtime, so prove() is non-circular:
#
#   * clean config (all-oracle strategies) -> (0,0,0,0,0,0): a correct auditor
#     must report a green run and must NOT flag anything.
#   * one buggy strategy injected -> exactly one nonzero slot. The slot's value
#     is the count a faithful detector produces:
#       - stale handle re-render          -> stale_clicks = 1
#       - eager assert (no settle)         -> premature_assertions = 1
#       - click-before-change emitter      -> event_order_violations = 1
#       - silent 404 on unmocked URL       -> unmocked_silent = 1
#       - hydration-blind client render    -> hydration_mismatches = 6
#         (dropping the <h1> heading shifts the pre-order traversal: the heading
#          tag/role pair disappears and every following pair shifts, yielding a
#          structural diff of 6 against the server tree — a hand-counted literal)
#       - brittle absolute XPath           -> selector_breaks = 1
#
# prove(auditor) is True iff the auditor's vector diverges from ANY frozen
# literal — i.e. it misses a real flake (a slot that should be nonzero comes back
# 0) or cries wolf on the clean run (a slot that should be 0 comes back nonzero).
#
# Pure + deterministic: the DOM is immutable data, re-render is a pure tree
# mutation, async work is a manually-drained FIFO, "network" is a dict lookup.
# No real browser, socket, event loop, thread, clock, RNG, or filesystem.
#
# The two planted mutants model genuine real-world E2E auditor defects (per the
# campaign hint — a matcher that matches too broadly, and a flow that reports
# pass despite a failed assertion):
#
#   * broad_selector_auditor — judges the stale/brittle click by node-id presence
#     ALONE, ignoring whether the resolved node is the intended target. A stale
#     handle re-resolves to a still-present (but detached/wrong) node id, so the
#     over-broad matcher reports stale_clicks=0 and the brittle XPath that lands
#     on a SHIFTED node also slips through -> the stale-handle and brittle-XPath
#     flakes go undetected (matcher matches too broadly).
#   * green_flow_auditor — runs the flow but reports premature_assertions=0 and
#     unmocked_silent=0 unconditionally: it declares the run green even though an
#     assertion fired before async work settled and an unmocked request returned
#     a silent 404 (a flow that reports pass despite a failed assertion).
# ---------------------------------------------------------------------------


# The fixed slot order of the failure vector a correct auditor reports.
_VEC_SLOTS = ("stale_clicks", "premature_assertions", "event_order_violations",
              "unmocked_silent", "hydration_mismatches", "selector_breaks")


@dataclass(frozen=True)
class E2ECase:
    """One frozen E2E config with a literal, hand-derived expected failure vector."""
    name: str
    # which single strategy slot to make buggy ("" => the all-clean config)
    buggy_slot: str
    expected_vec: tuple[int, int, int, int, int, int]
    note: str = ""


# A correct auditor must reproduce every literal vector below. Each vector is
# hand-derived from the per-dimension contract (NOT read from the oracle): a
# clean run is all-zeros; each injected flake lights up exactly one slot.
E2E_CORPUS: tuple[E2ECase, ...] = (
    E2ECase("clean_run", "", (0, 0, 0, 0, 0, 0),
            "all-oracle strategies: a correct auditor must report a green run"),
    E2ECase("stale_handle", "clicker", (1, 0, 0, 0, 0, 0),
            "cached pre-render handle clicks a detached node -> stale_clicks=1"),
    E2ECase("eager_assert", "settle_strategy", (0, 1, 0, 0, 0, 0),
            "assertion runs before async work settles -> premature_assertions=1"),
    E2ECase("reordered_events", "emitter", (0, 0, 1, 0, 0, 0),
            "click fires before change -> one event_order_violations"),
    E2ECase("silent_404", "fetch_impl", (0, 0, 0, 1, 0, 0),
            "unmocked URL returns a silent 404 instead of raising -> unmocked_silent=1"),
    E2ECase("hydration_mismatch", "renderer", (0, 0, 0, 0, 6, 0),
            "client render drops the <h1> -> pre-order diff of 6 vs the server tree"),
    E2ECase("brittle_xpath", "selector", (0, 0, 0, 0, 0, 1),
            "absolute XPath valid only pre-render -> selector_breaks=1"),
)


# The buggy strategy injected for each non-clean corpus slot. Reuses the
# harness's own planted buggy implementations.
_BUGGY_STRATEGY = {
    "clicker": stale_clicker,
    "settle_strategy": no_settle,
    "emitter": event_emitter_reordered,
    "fetch_impl": silent_404_fetch,
    "renderer": hydration_blind_render,
    "selector": brittle_xpath_selector,
}


def _config_for(case: E2ECase) -> dict:
    """Build the six-strategy kwargs for a corpus case (clean except its one
    injected buggy slot, if any)."""
    cfg: dict = {}
    if case.buggy_slot:
        cfg[case.buggy_slot] = _BUGGY_STRATEGY[case.buggy_slot]
    return cfg


# --- ORACLE: reuse the harness's own correct `audit` as the auditor ----------

def oracle_auditor(**strategies) -> tuple[int, int, int, int, int, int]:
    """Correct E2E auditor: delegates to the harness's own ``audit`` and returns
    the six failure counts as a fixed-order vector. A clean config yields all
    zeros; each injected flake lights up exactly its own slot."""
    rep = audit(**strategies)
    return tuple(getattr(rep, slot) for slot in _VEC_SLOTS)  # type: ignore[return-value]


# --- Planted buggy twins (each models a real E2E auditor defect) -------------

def broad_selector_auditor(**strategies) -> tuple[int, int, int, int, int, int]:
    """BUG: judges click/selector success by node-id PRESENCE alone, ignoring
    whether the resolved node is the intended target — a matcher that matches too
    broadly.

    A stale handle re-resolves to a node id that is gone from the new tree OR (for
    the brittle XPath) to a *different* still-present node; checking only "is some
    id present / did resolution return anything" passes both, so the stale-handle
    and brittle-XPath flakes are reported as clean (stale_clicks / selector_breaks
    stay 0 when they should fire).
    """
    clicker = strategies.get("clicker", robust_clicker)
    selector = strategies.get("selector", oracle_selector)
    # over-broad: a click "succeeds" if the cached id is non-None (matches too
    # broadly — never checks the id is still in the live tree / is the target)
    nid = resolve(SUBMIT_SELECTOR, INITIAL_DOM) if clicker is stale_clicker \
        else resolve(SUBMIT_SELECTOR, MUTATED_DOM)
    stale_clicks = 0 if nid is not None else 1
    # over-broad: a selector "works" if it resolves to ANY node, not the target
    sel_nid = selector(MUTATED_DOM)
    selector_breaks = 0 if sel_nid is not None else 1
    return (
        stale_clicks,
        _check_premature(strategies.get("settle_strategy", oracle_settle)),
        count_event_order_violations(strategies.get("emitter", event_emitter_oracle)()),
        _check_unmocked(strategies.get("fetch_impl", oracle_fetch)),
        hydration_diff(INITIAL_DOM, strategies.get("renderer", faithful_render)(INITIAL_DOM)),
        selector_breaks,
    )


def green_flow_auditor(**strategies) -> tuple[int, int, int, int, int, int]:
    """BUG: reports the run green on the timing/network dimensions no matter what —
    a flow that declares pass despite a failed assertion.

    It hard-codes premature_assertions=0 and unmocked_silent=0, so an assertion
    that fired before async work settled and an unmocked request that returned a
    silent 404 both slip through as a passing run.
    """
    rep = audit(**strategies)
    return (
        rep.stale_clicks,
        0,  # BUG: always reports the assertion settled, even when it did not
        rep.event_order_violations,
        0,  # BUG: always reports the network was clean, even on a silent 404
        rep.hydration_mismatches,
        rep.selector_breaks,
    )


def prove(impl: Callable[..., tuple]) -> bool:
    """True iff ``impl`` (an E2E auditor) MIS-JUDGES any frozen corpus case (i.e.
    the bug is caught): its failure vector diverges from the hand-derived literal
    — it misses a real flake (a slot that must fire comes back 0) or cries wolf on
    the clean run (a slot that must be 0 comes back nonzero), or returns the wrong
    shape / raises.

    Non-circular + deterministic: every expectation is a literal baked into
    E2E_CORPUS, never read from the oracle; immutable data + dict lookups only,
    no RNG/clock/network/socket/event-loop/filesystem. An impl that raises on a
    corpus case counts as caught.
    """
    for case in E2E_CORPUS:
        try:
            vec = tuple(impl(**_config_for(case)))
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if vec != case.expected_vec:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_auditor,
    mutants=(
        Mutant("broad_selector_auditor", broad_selector_auditor,
               "judges click/selector success by node-id presence alone (matches "
               "too broadly) -> stale-handle and brittle-XPath flakes go undetected"),
        Mutant("green_flow_auditor", green_flow_auditor,
               "hard-codes premature_assertions=0 and unmocked_silent=0 -> reports "
               "the run green despite an eager assertion and a silent 404"),
    ),
    corpus_size=len(E2E_CORPUS),
    kind="oracle_swap",
    notes="an E2E auditor must catch every flake class (stale handle, premature "
          "assertion, out-of-order events, silent 404, hydration mismatch, brittle "
          "selector) without crying wolf on a clean run; expected failure vectors "
          "are frozen literals, never read from the oracle",
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


def s_oracle_run_clean() -> Check:
    rep = audit()
    return _chk("oracle_run_clean", rep.meets_floors() and rep.total_failures == 0,
                f"total_failures={rep.total_failures}")


def s_selector_resolves_by_role() -> Check:
    return _chk("selector_resolves_by_role",
                resolve(Selector("role", "button"), INITIAL_DOM) == "submit")


def s_selector_resolves_by_testid() -> Check:
    return _chk("selector_resolves_by_testid",
                resolve(Selector("testid", "submit-btn"), INITIAL_DOM) == "submit")


def s_stale_handle_reresolved() -> Check:
    return _chk("stale_handle_reresolved",
                robust_clicker(INITIAL_DOM, MUTATED_DOM, SUBMIT_SELECTOR))


def s_stale_handle_clicker_caught() -> Check:
    rep = audit(clicker=stale_clicker)
    return _chk("stale_handle_clicker_caught", rep.stale_clicks >= 1,
                f"stale_clicks={rep.stale_clicks}")


def s_assert_after_settle_ok() -> Check:
    loop = EventLoop()
    loop.schedule(lambda: None)
    loop.settle()
    try:
        assert_settled(loop)
        return _chk("assert_after_settle_ok", True)
    except PrematureAssertionError:
        return _chk("assert_after_settle_ok", False, "raised unexpectedly")


def s_assert_before_settle_fails() -> Check:
    loop = EventLoop()
    loop.schedule(lambda: None)
    try:
        assert_settled(loop)
        return _chk("assert_before_settle_fails", False, "did not raise")
    except PrematureAssertionError:
        return _chk("assert_before_settle_fails", True)


def s_eager_asserter_caught() -> Check:
    rep = audit(settle_strategy=no_settle)
    return _chk("eager_asserter_caught", rep.premature_assertions >= 1,
                f"premature={rep.premature_assertions}")


def s_event_order_change_before_click() -> Check:
    return _chk("event_order_change_before_click",
                count_event_order_violations(event_emitter_oracle()) == 0)


def s_event_order_focus_before_input() -> Check:
    ev = event_emitter_oracle()
    seq = {e.kind: e.seq for e in ev}
    return _chk("event_order_focus_before_input", seq["focus"] < seq["input"])


def s_reordered_emitter_caught() -> Check:
    rep = audit(emitter=event_emitter_reordered)
    return _chk("reordered_emitter_caught", rep.event_order_violations >= 1,
                f"order_violations={rep.event_order_violations}")


def s_unmocked_request_raises() -> Check:
    try:
        oracle_fetch(UNMOCKED_URL, ROUTES)
        return _chk("unmocked_request_raises", False, "did not raise")
    except UnmockedRequestError:
        return _chk("unmocked_request_raises", True)


def s_mocked_request_returns_response() -> Check:
    return _chk("mocked_request_returns_response",
                oracle_fetch("/api/user", ROUTES).status == 200)


def s_silent_404_caught() -> Check:
    rep = audit(fetch_impl=silent_404_fetch)
    return _chk("silent_404_caught", rep.unmocked_silent >= 1,
                f"unmocked_silent={rep.unmocked_silent}")


def s_hydration_match_clean() -> Check:
    return _chk("hydration_match_clean",
                hydration_diff(INITIAL_DOM, faithful_render(INITIAL_DOM)) == 0)


def s_hydration_mismatch_detected() -> Check:
    d = hydration_diff(INITIAL_DOM, hydration_blind_render(INITIAL_DOM))
    return _chk("hydration_mismatch_detected", d >= 1, f"diff={d}")


def s_hydration_blind_caught() -> Check:
    rep = audit(renderer=hydration_blind_render)
    return _chk("hydration_blind_caught", rep.hydration_mismatches >= 1,
                f"hydration_mismatches={rep.hydration_mismatches}")


def s_testid_stable_on_rerender() -> Check:
    nid = oracle_selector(MUTATED_DOM)
    return _chk("testid_stable_on_rerender",
                nid == "submit_v2" and MUTATED_DOM.nodes[nid].testid == "submit-btn", str(nid))


def s_xpath_breaks_on_rerender() -> Check:
    nid = brittle_xpath_selector(MUTATED_DOM)
    node = MUTATED_DOM.nodes.get(nid) if nid else None
    broke = node is None or node.testid != "submit-btn"
    return _chk("xpath_breaks_on_rerender", broke, f"resolved={nid}")


def s_brittle_xpath_caught() -> Check:
    rep = audit(selector=brittle_xpath_selector)
    return _chk("brittle_xpath_caught", rep.selector_breaks >= 1,
                f"selector_breaks={rep.selector_breaks}")


def s_navigation_settles_before_assert() -> Check:
    loop = EventLoop()
    navigate(loop, "/page2")
    pending_before = len(loop.pending) >= 1
    loop.settle()
    return _chk("navigation_settles_before_assert",
                pending_before and loop.pending == [])


def s_loop_drains_all_pending() -> Check:
    loop = EventLoop()
    for _ in range(3):
        loop.schedule(lambda: None)
    loop.settle()
    return _chk("loop_drains_all_pending", loop.pending == [])


SCENARIOS: dict[str, Callable[[], Check]] = {
    f.__name__[2:]: f
    for f in [
        s_oracle_run_clean,
        s_selector_resolves_by_role,
        s_selector_resolves_by_testid,
        s_stale_handle_reresolved,
        s_stale_handle_clicker_caught,
        s_assert_after_settle_ok,
        s_assert_before_settle_fails,
        s_eager_asserter_caught,
        s_event_order_change_before_click,
        s_event_order_focus_before_input,
        s_reordered_emitter_caught,
        s_unmocked_request_raises,
        s_mocked_request_returns_response,
        s_silent_404_caught,
        s_hydration_match_clean,
        s_hydration_mismatch_detected,
        s_hydration_blind_caught,
        s_testid_stable_on_rerender,
        s_xpath_breaks_on_rerender,
        s_brittle_xpath_caught,
        s_navigation_settles_before_assert,
        s_loop_drains_all_pending,
    ]
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(verbose: bool = False, as_json: bool = False) -> int:
    """Run every scenario AND the teeth swap-check through a fail-loud ``Report``.

    Keeps all of the harness's meaningful per-dimension scenario checks, then
    asserts the teeth (prove(oracle) is False and every planted auditor mutant is
    caught). Returns 0 green / 1 on any failure."""
    report = Report("core/browser_e2e")

    # 1. Every existing scenario becomes a recorded check (preserved verbatim).
    for name, fn in SCENARIOS.items():
        chk = fn()
        report.record(chk.name, chk.passed, detail=chk.detail)
        if verbose and not as_json and chk.passed:
            print(f"  OK    {chk.name:40s} {chk.detail}")

    # 2. The correct oracle auditor reproduces every frozen failure vector and
    #    does not cry wolf on the clean run.
    for case in E2E_CORPUS:
        vec = oracle_auditor(**_config_for(case))
        report.add(f"oracle_vec:{case.name}", case.expected_vec, tuple(vec),
                   detail=case.note)

    # 3. Teeth: prove(oracle) is False AND every planted auditor mutant is caught.
    report.assert_teeth(TEETH)

    return report.emit(as_json=as_json)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic browser/E2E surrogate harness")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="emit machine-readable findings (implies --self-test)")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--verbose", action="store_true")
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
