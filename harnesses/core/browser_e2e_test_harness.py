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
from dataclasses import dataclass
from typing import Callable, Optional

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


def resolve(selector: Selector, dom: Dom) -> Optional[str]:
    if selector.kind == "xpath":
        return _resolve_xpath(dom, selector.value)
    attr = {"role": "role", "label": "label", "testid": "testid"}[selector.kind]
    for nid, n in dom.nodes.items():
        if getattr(n, attr) == selector.value:
            return nid
    return None


def _resolve_xpath(dom: Dom, path: str) -> Optional[str]:
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


def oracle_selector(dom: Dom) -> Optional[str]:
    return resolve(Selector("testid", "submit-btn"), dom)


def brittle_xpath_selector(dom: Dom) -> Optional[str]:
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


def _check_selector(strategy: Callable[[Dom], Optional[str]]) -> int:
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


def _run_self_test(verbose: bool = False) -> int:
    results = [fn() for fn in SCENARIOS.values()]
    failures = [r for r in results if not r.passed]
    for r in results:
        if verbose or not r.passed:
            mark = "OK  " if r.passed else "FAIL"
            print(f"  {mark}  {r.name:40s} {r.detail}")
    if failures:
        print(f"FAILED: {len(failures)}/{len(results)}", file=sys.stderr)
        return 1
    print(f"OK: {len(results)} scenarios passed.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Deterministic browser/E2E surrogate harness")
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
