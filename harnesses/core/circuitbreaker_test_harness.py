#!/usr/bin/env python3
"""circuitbreaker_test_harness.py â€” Circuit Breaker Resilience Harness (2026)
================================================================================
Pure-Python (ZERO dependencies) harness for testing the circuit-breaker
resilience pattern.

Distinct from ratelimit_test_harness (#28) which caps request *rate*; this
harness models *failure-driven* state transitions:

  - CLOSED  -> OPEN      after `failure_threshold` consecutive failures
  - OPEN    -> HALF_OPEN after `reset_timeout` seconds have elapsed
  - HALF_OPEN -> CLOSED  after `success_threshold` trial successes
  - HALF_OPEN -> OPEN    on the first trial failure (re-trips immediately)

Hotspots exercised:
  - Time is INJECTED via a `clock` callable so transitions are deterministic
    (no real sleeps in tests).
  - Half-open admits at most `half_open_max_calls` trial calls; excess calls
    are rejected with CircuitOpenError while a trial is in flight.
  - A single success in CLOSED resets the consecutive-failure counter (so
    intermittent failures never trip the breaker).
  - CircuitBreakerOracle replays the same event log with an independent
    functional fold to provide ground truth.

Port: 19330

Usage:
  python circuitbreaker_test_harness.py --self-test
  python circuitbreaker_test_harness.py --mock-server --port 19330
  python circuitbreaker_test_harness.py --self-test --verbose
"""

import argparse
import contextlib
import json
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path as _Path
from urllib.parse import parse_qs, urlparse

if __package__ in {None, ""}:
    _ROOT = _Path(__file__).resolve().parents[2]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

from harnesses._teeth import (
    Mutant,
    Report,
    Teeth,
    emit_legacy_self_test,
    serve_mock_server_until_interrupt,
)

CLOSED = "CLOSED"
OPEN = "OPEN"
HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the breaker is not accepting traffic."""


class FakeClock:
    """Deterministic monotonic clock for tests. advance(seconds) moves time forward."""

    def __init__(self, start=0.0):
        self._t = float(start)

    def __call__(self):
        return self._t

    def advance(self, seconds):
        self._t += float(seconds)
        return self._t


# ============================================================
# CIRCUIT BREAKER
# ============================================================

class CircuitBreaker:
    """Failure-driven circuit breaker with an injectable clock."""

    def __init__(self, failure_threshold=5, reset_timeout=30.0,
                 half_open_max_calls=1, success_threshold=1, clock=None):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        self.failure_threshold = failure_threshold
        self.reset_timeout = float(reset_timeout)
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        import time as _time
        self.clock = clock if clock is not None else _time.monotonic

        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_calls = 0
        self._half_open_successes = 0

    # -- introspection -------------------------------------------------

    @property
    def state(self):
        """Logical state, accounting for an elapsed reset_timeout.

        Reading state while OPEN does NOT itself admit a call; it only reports
        that the breaker is *eligible* to move to HALF_OPEN. The transition is
        committed inside allow()/call() when a real trial call arrives.
        """
        if self._state == OPEN and self._reset_elapsed():
            return HALF_OPEN
        return self._state

    def _reset_elapsed(self):
        return self._opened_at is not None and \
            (self.clock() - self._opened_at) >= self.reset_timeout

    # -- gate ----------------------------------------------------------

    def allow(self):
        """Return True if a call may proceed, mutating state as needed.

        Commits the OPEN -> HALF_OPEN transition on the first eligible call,
        and enforces the half-open trial cap.
        """
        if self._state == OPEN:
            if self._reset_elapsed():
                self._to_half_open()
            else:
                return False
        if self._state == HALF_OPEN:
            if self._half_open_calls >= self.half_open_max_calls:
                return False
            self._half_open_calls += 1
            return True
        return True  # CLOSED

    def call(self, func, *args, **kwargs):
        """Execute func under the breaker. Raises CircuitOpenError if rejected."""
        if not self.allow():
            raise CircuitOpenError(f"circuit is {self.state}")
        try:
            result = func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    # -- transitions ---------------------------------------------------

    def record_success(self):
        if self._state == HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.success_threshold:
                self._to_closed()
        elif self._state == CLOSED:
            self._consecutive_failures = 0

    def record_failure(self):
        if self._state == HALF_OPEN:
            self._to_open()  # any failure during trial re-trips
        elif self._state == CLOSED:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.failure_threshold:
                self._to_open()

    def _to_open(self):
        self._state = OPEN
        self._opened_at = self.clock()
        self._half_open_calls = 0
        self._half_open_successes = 0

    def _to_half_open(self):
        self._state = HALF_OPEN
        self._half_open_calls = 0
        self._half_open_successes = 0

    def _to_closed(self):
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at = None
        self._half_open_calls = 0
        self._half_open_successes = 0


# ============================================================
# ORACLE (independent functional fold)
# ============================================================

class CircuitBreakerOracle:
    """Ground-truth state computed by folding an event log.

    Events are tuples:
      ("fail",)            -> a failed call
      ("ok",)              -> a successful call
      ("advance", seconds) -> move the clock forward

    Returns the final logical state string.
    """

    @staticmethod
    def final_state(events, failure_threshold=5, reset_timeout=30.0,
                    half_open_max_calls=1, success_threshold=1):
        state = CLOSED
        fails = 0
        opened_at = None
        now = 0.0
        half_calls = 0
        half_ok = 0

        def maybe_promote():
            nonlocal state, half_calls, half_ok
            if state == OPEN and opened_at is not None and \
                    (now - opened_at) >= reset_timeout:
                state = HALF_OPEN
                half_calls = 0
                half_ok = 0

        for ev in events:
            kind = ev[0]
            if kind == "advance":
                now += float(ev[1])
                continue
            # a call event: first promote if eligible
            maybe_promote()
            if state == OPEN:
                continue  # call rejected; no state change
            if state == HALF_OPEN:
                if half_calls >= half_open_max_calls:
                    continue  # rejected trial overflow
                half_calls += 1
                if kind == "ok":
                    half_ok += 1
                    if half_ok >= success_threshold:
                        state = CLOSED
                        fails = 0
                        opened_at = None
                else:  # fail
                    state = OPEN
                    opened_at = now
                continue
            # CLOSED
            if kind == "ok":
                fails = 0
            else:
                fails += 1
                if fails >= failure_threshold:
                    state = OPEN
                    opened_at = now
        # report logical state (account for elapsed timeout at the end)
        if state == OPEN and opened_at is not None and \
                (now - opened_at) >= reset_timeout:
            return HALF_OPEN
        return state


# ============================================================
# TEETH: frozen event-log audits + planted transition defects
# ============================================================

@dataclass(frozen=True)
class CircuitBreakerAuditConfig:
    failure_threshold: int = 3
    reset_timeout: float = 10.0
    half_open_max_calls: int = 1
    success_threshold: int = 1


@dataclass(frozen=True)
class CircuitBreakerAuditCase:
    name: str
    events: tuple[tuple, ...]
    config: CircuitBreakerAuditConfig
    expected_events: tuple[str, ...]


def _state_event(state):
    return (f"final:{state}",)


def _audit_state(events, config):
    return CircuitBreakerOracle.final_state(
        events,
        failure_threshold=config.failure_threshold,
        reset_timeout=config.reset_timeout,
        half_open_max_calls=config.half_open_max_calls,
        success_threshold=config.success_threshold,
    )


CIRCUIT_BREAKER_AUDIT_CORPUS = (
    CircuitBreakerAuditCase(
        name="opens_at_failure_threshold",
        events=(("fail",), ("fail",), ("fail",)),
        config=CircuitBreakerAuditConfig(failure_threshold=3),
        expected_events=_state_event(OPEN),
    ),
    CircuitBreakerAuditCase(
        name="success_resets_consecutive_failures",
        events=(("fail",), ("fail",), ("ok",), ("fail",), ("fail",)),
        config=CircuitBreakerAuditConfig(failure_threshold=3),
        expected_events=_state_event(CLOSED),
    ),
    CircuitBreakerAuditCase(
        name="half_open_success_closes_after_timeout",
        events=(("fail",), ("advance", 10.0), ("ok",)),
        config=CircuitBreakerAuditConfig(failure_threshold=1, reset_timeout=10.0),
        expected_events=_state_event(CLOSED),
    ),
    CircuitBreakerAuditCase(
        name="half_open_failure_retrips_open",
        events=(("fail",), ("advance", 10.0), ("fail",)),
        config=CircuitBreakerAuditConfig(failure_threshold=1, reset_timeout=10.0),
        expected_events=_state_event(OPEN),
    ),
    CircuitBreakerAuditCase(
        name="half_open_trial_cap_blocks_second_probe",
        events=(("fail",), ("advance", 10.0), ("ok",), ("ok",)),
        config=CircuitBreakerAuditConfig(
            failure_threshold=1,
            reset_timeout=10.0,
            half_open_max_calls=1,
            success_threshold=2,
        ),
        expected_events=_state_event(HALF_OPEN),
    ),
    CircuitBreakerAuditCase(
        name="open_state_rejects_before_timeout",
        events=(("fail",), ("ok",)),
        config=CircuitBreakerAuditConfig(failure_threshold=1, reset_timeout=10.0),
        expected_events=_state_event(OPEN),
    ),
)


def oracle_circuitbreaker_audit(case):
    return _state_event(_audit_state(case.events, case.config))


def threshold_one_late_circuit_auditor(case):
    if case.name != "opens_at_failure_threshold":
        return oracle_circuitbreaker_audit(case)
    cfg = CircuitBreakerAuditConfig(
        failure_threshold=case.config.failure_threshold + 1,
        reset_timeout=case.config.reset_timeout,
        half_open_max_calls=case.config.half_open_max_calls,
        success_threshold=case.config.success_threshold,
    )
    return _state_event(_audit_state(case.events, cfg))


def success_reset_blind_circuit_auditor(case):
    if case.name != "success_resets_consecutive_failures":
        return oracle_circuitbreaker_audit(case)
    mutated = tuple(("fail",) if ev[0] == "ok" else ev for ev in case.events)
    return _state_event(_audit_state(mutated, case.config))


def half_open_failure_closes_circuit_auditor(case):
    if case.name != "half_open_failure_retrips_open":
        return oracle_circuitbreaker_audit(case)
    return _state_event(CLOSED)


def half_open_cap_ignored_circuit_auditor(case):
    if case.name != "half_open_trial_cap_blocks_second_probe":
        return oracle_circuitbreaker_audit(case)
    cfg = CircuitBreakerAuditConfig(
        failure_threshold=case.config.failure_threshold,
        reset_timeout=case.config.reset_timeout,
        half_open_max_calls=case.config.half_open_max_calls + 1,
        success_threshold=case.config.success_threshold,
    )
    return _state_event(_audit_state(case.events, cfg))


def open_window_blind_circuit_auditor(case):
    if case.name != "open_state_rejects_before_timeout":
        return oracle_circuitbreaker_audit(case)
    return _state_event(CLOSED)


def prove(impl: Callable[[CircuitBreakerAuditCase], tuple[str, ...]]) -> bool:
    return any(impl(case) != case.expected_events for case in CIRCUIT_BREAKER_AUDIT_CORPUS)


TEETH = Teeth(
    prove=prove,
    oracle=oracle_circuitbreaker_audit,
    mutants=(
        Mutant("threshold_one_late_circuit_auditor", threshold_one_late_circuit_auditor,
               "opens one failure later than the configured threshold"),
        Mutant("success_reset_blind_circuit_auditor", success_reset_blind_circuit_auditor,
               "does not reset the consecutive-failure counter after a success"),
        Mutant("half_open_failure_closes_circuit_auditor", half_open_failure_closes_circuit_auditor,
               "closes the circuit after a failed half-open probe"),
        Mutant("half_open_cap_ignored_circuit_auditor", half_open_cap_ignored_circuit_auditor,
               "allows an extra half-open probe and reaches CLOSED too early"),
        Mutant("open_window_blind_circuit_auditor", open_window_blind_circuit_auditor,
               "serves traffic while OPEN before the reset timeout elapses"),
    ),
    corpus_size=len(CIRCUIT_BREAKER_AUDIT_CORPUS),
    kind="auditor",
    notes="Frozen circuit-breaker event logs for threshold, reset, half-open, cap, and open-window behavior.",
)


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class CircuitHandler(BaseHTTPRequestHandler):
    breaker = None
    clock = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/state":
            self._json({"state": CircuitHandler.breaker.state})
            return
        if parsed.path == "/advance":
            secs = float(params.get("seconds", ["0"])[0])
            CircuitHandler.clock.advance(secs)
            self._json({"state": CircuitHandler.breaker.state})
            return
        if parsed.path in ("/ok", "/fail"):
            allowed = CircuitHandler.breaker.allow()
            if not allowed:
                self._json({"rejected": True, "state": CircuitHandler.breaker.state}, code=503)
                return
            if parsed.path == "/ok":
                CircuitHandler.breaker.record_success()
            else:
                CircuitHandler.breaker.record_failure()
            self._json({"rejected": False, "state": CircuitHandler.breaker.state})
            return
        self.send_response(404)
        self.end_headers()

    def _json(self, obj, code=200):
        resp = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19330):
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=3, reset_timeout=10.0, clock=clock)
    CircuitHandler.breaker = breaker
    CircuitHandler.clock = clock
    server = ThreadingHTTPServer(("127.0.0.1", port), CircuitHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

class CBTestResult:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail

    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n        {self.detail}"
        return msg


def run_all_scenarios(verbose=False):
    results = []

    def check(name, cond, detail=""):
        r = CBTestResult(name, bool(cond), detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    def boom():
        raise RuntimeError("downstream failure")

    # 1. Starts CLOSED
    cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
    check("1. New breaker starts CLOSED", cb.state == CLOSED, cb.state)

    # 2. Trips to OPEN after threshold consecutive failures
    cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            cb.call(boom)
    check("2. OPEN after 3 consecutive failures", cb.state == OPEN, cb.state)

    # 3. Below threshold stays CLOSED
    cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            cb.call(boom)
    check("3. Stays CLOSED below threshold (2<3)", cb.state == CLOSED, cb.state)

    # 4. A success resets the failure counter
    cb = CircuitBreaker(failure_threshold=3, clock=FakeClock())
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    cb.call(lambda: "ok")          # reset
    with contextlib.suppress(RuntimeError):
        cb.call(boom)              # only 1 failure since reset
    check("4. Success resets counter (1 fail after reset != trip)",
          cb.state == CLOSED, cb.state)

    # 5. OPEN rejects calls with CircuitOpenError
    clk = FakeClock()
    cb = CircuitBreaker(failure_threshold=2, reset_timeout=10.0, clock=clk)
    for _ in range(2):
        with contextlib.suppress(RuntimeError):
            cb.call(boom)
    rejected = False
    try:
        cb.call(lambda: "ok")
    except CircuitOpenError:
        rejected = True
    check("5. OPEN rejects calls with CircuitOpenError", rejected)

    # 6. After reset_timeout, logical state is HALF_OPEN
    clk.advance(10.0)
    check("6. HALF_OPEN after reset_timeout elapses", cb.state == HALF_OPEN, cb.state)

    # 7. Trial success in HALF_OPEN closes the breaker
    cb.call(lambda: "ok")
    check("7. Trial success closes breaker", cb.state == CLOSED, cb.state)

    # 8. Trial failure in HALF_OPEN re-trips to OPEN
    clk2 = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0, clock=clk2)
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    clk2.advance(5.0)                # -> HALF_OPEN
    with contextlib.suppress(RuntimeError):
        cb.call(boom)               # trial fails
    check("8. Trial failure re-trips to OPEN", cb.state == OPEN, cb.state)

    # 9. Half-open admits at most half_open_max_calls trials
    clk3 = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0,
                        half_open_max_calls=1, success_threshold=2, clock=clk3)
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    clk3.advance(5.0)               # -> HALF_OPEN
    first = cb.allow()             # admits the single trial
    second = cb.allow()           # over the cap -> rejected
    check("9. Half-open caps concurrent trials (1 admit, 1 reject)",
          first is True and second is False, f"first={first} second={second}")

    # 10. success_threshold>1 requires multiple trial successes to close
    clk4 = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=5.0,
                        half_open_max_calls=3, success_threshold=2, clock=clk4)
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    clk4.advance(5.0)
    cb.call(lambda: "ok")          # 1st trial success
    mid = cb.state
    cb.call(lambda: "ok")          # 2nd trial success -> close
    check("10. success_threshold=2 closes only after 2 trials",
          mid == HALF_OPEN and cb.state == CLOSED, f"mid={mid} end={cb.state}")

    # 11. Oracle agreement on a mixed event log
    events = [("fail",), ("fail",), ("ok",), ("fail",), ("fail",), ("fail",),
              ("advance", 30.0), ("ok",)]
    expected = CircuitBreakerOracle.final_state(
        events, failure_threshold=3, reset_timeout=30.0,
        half_open_max_calls=1, success_threshold=1)
    clk5 = FakeClock()
    cb = CircuitBreaker(failure_threshold=3, reset_timeout=30.0, clock=clk5)
    for ev in events:
        if ev[0] == "advance":
            clk5.advance(ev[1])
        elif ev[0] == "ok":
            with contextlib.suppress(CircuitOpenError):
                cb.call(lambda: "ok")
        else:
            with contextlib.suppress(RuntimeError, CircuitOpenError):
                cb.call(boom)
    check("11. Oracle agrees on mixed event log",
          cb.state == expected, f"breaker={cb.state} oracle={expected}")

    # 12. Reading state while OPEN before timeout does not admit a call
    clk6 = FakeClock()
    cb = CircuitBreaker(failure_threshold=1, reset_timeout=10.0, clock=clk6)
    with contextlib.suppress(RuntimeError):
        cb.call(boom)
    clk6.advance(5.0)               # not yet elapsed
    still_open = cb.state == OPEN
    rejected = not cb.allow()
    check("12. OPEN before timeout stays closed-gate",
          still_open and rejected, f"state={cb.state}")

    # 13. Invalid config rejected
    bad = False
    try:
        CircuitBreaker(failure_threshold=0)
    except ValueError:
        bad = True
    check("13. failure_threshold=0 rejected", bad)

    return results


def _emit_self_test_report(results):
    report = Report("core/circuitbreaker")
    for result in results:
        report.record(result.name, result.passed, detail=result.detail)
    for case in CIRCUIT_BREAKER_AUDIT_CORPUS:
        report.add(
            f"oracle_circuitbreaker_audit:{case.name}",
            list(case.expected_events),
            list(oracle_circuitbreaker_audit(case)),
        )
    report.assert_teeth(TEETH)
    return report.emit()


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="circuitbreaker_test_harness",
        description="Circuit-breaker resilience harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only")
    p.add_argument("--port", type=int, default=19330,
                   help="Mock server port (default: 19330)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        return serve_mock_server_until_interrupt(start_mock_server, args.port,
                                                "Circuit-breaker mock server")

    if args.self_test:
        return emit_legacy_self_test("CIRCUIT BREAKER TEST HARNESS",
                                     run_all_scenarios,
                                     args.verbose,
                                     _emit_self_test_report)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
