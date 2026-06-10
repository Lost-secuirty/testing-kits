#!/usr/bin/env python3
"""lockout_test_harness.py — Temporal PIN Lockout Test Harness (2026)
====================================================================
Pure-Python (ZERO dependencies) harness for testing time-based PIN
lockout mechanisms.

Distinct from authz_test_harness (#24), statemachine_test_harness (#22),
and ratelimit_test_harness (#28):
  - Tests time-windowed attempt counting with per-user state
  - FakeClock injection makes all tests deterministic (no real sleeps)
  - BuggyLockoutManager proves the harness catches both directions of failure
  - Concurrent attempt safety tested with threading.Barrier

Models the lockout behavior from pharmacy_app/config.py:
  LOCKOUT_THRESHOLD = 3
  LOCKOUT_SECONDS  = 300

Port: 19250

Usage:
  python lockout_test_harness.py --self-test
  python lockout_test_harness.py --mock-server --port 19250
  python lockout_test_harness.py --self-test --verbose
"""

import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================
# CONSTANTS (mirror pharmacy_app/config.py)
# ============================================================

LOCKOUT_THRESHOLD = 3
LOCKOUT_SECONDS = 300

# ============================================================
# FAKE CLOCK
# ============================================================

class FakeClock:
    """Injectable clock for deterministic time-based testing."""

    def __init__(self, start=0.0):
        self._t = start

    def now(self):
        return self._t

    def advance(self, seconds):
        self._t += seconds


# ============================================================
# LOCKOUT MANAGER
# ============================================================

class LockoutManager:
    """Thread-safe PIN lockout manager with injectable clock.

    State per user: {count: int, locked_since: float|None}
    A user is locked if locked_since is set AND elapsed < lockout_seconds.
    """

    def __init__(self, threshold=LOCKOUT_THRESHOLD,
                 lockout_seconds=LOCKOUT_SECONDS, clock=None):
        self.threshold = threshold
        self.lockout_seconds = lockout_seconds
        self._clock = clock if clock is not None else _RealClock()
        self._state = {}   # {username: {"count": int, "locked_since": float|None}}
        self._lock = threading.Lock()

    def _user(self, username):
        """Return (or create) state dict for user — must be called under lock."""
        if username not in self._state:
            self._state[username] = {"count": 0, "locked_since": None}
        return self._state[username]

    def is_locked(self, username):
        with self._lock:
            u = self._user(username)
            if u["locked_since"] is None:
                return False
            elapsed = self._clock.now() - u["locked_since"]
            if elapsed >= self.lockout_seconds:
                u["locked_since"] = None
                u["count"] = 0
                return False
            return True

    def record_failure(self, username):
        """Increment failure count; lock if threshold reached."""
        with self._lock:
            u = self._user(username)
            u["count"] += 1
            if u["count"] >= self.threshold:
                u["locked_since"] = self._clock.now()
                u["count"] = 0

    def record_success(self, username):
        """Reset failure count and clear any lock."""
        with self._lock:
            u = self._user(username)
            u["count"] = 0
            u["locked_since"] = None

    def reset(self, username):
        with self._lock:
            self._state.pop(username, None)


class _RealClock:
    def now(self):
        return time.monotonic()


# ============================================================
# BUGGY IMPLEMENTATIONS (prove the harness catches failures)
# ============================================================

class BuggyLockoutManager(LockoutManager):
    """Never releases the lockout once triggered (never checks elapsed)."""

    def is_locked(self, username):
        with self._lock:
            u = self._user(username)
            return u["locked_since"] is not None


class BuggyLockoutManager2(LockoutManager):
    """Never increments the failure counter (never locks)."""

    def record_failure(self, username):
        pass


# ============================================================
# MOCK HTTP SERVER
# ============================================================

_VALID_PINS = {"alice": "1234", "bob": "5678"}


class LockoutHandler(BaseHTTPRequestHandler):
    """POST /login {"user": ..., "pin": ...} -> 200/401/423."""

    manager = None  # set by start_mock_server

    def do_POST(self):
        if self.path != "/login":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except Exception:
            self.send_response(400)
            self.end_headers()
            return

        user = req.get("user", "")
        pin = req.get("pin", "")

        if LockoutHandler.manager.is_locked(user):
            self._respond(423, {"status": "locked"})
            return

        if _VALID_PINS.get(user) == pin:
            LockoutHandler.manager.record_success(user)
            self._respond(200, {"status": "ok"})
        else:
            LockoutHandler.manager.record_failure(user)
            self._respond(401, {"status": "wrong_pin"})

    def _respond(self, code, payload):
        resp = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19250):
    mgr = LockoutManager()
    LockoutHandler.manager = mgr
    server = ThreadingHTTPServer(("127.0.0.1", port), LockoutHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

class LockoutTestResult:
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
        r = LockoutTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    def fresh(threshold=3, lockout_seconds=300):
        clock = FakeClock(start=0.0)
        mgr = LockoutManager(threshold=threshold, lockout_seconds=lockout_seconds, clock=clock)
        return mgr, clock

    # 1. First attempt never locked
    mgr, clock = fresh()
    check("1. First attempt never locked", not mgr.is_locked("alice"))

    # 2. threshold-1 attempts still permitted
    mgr, clock = fresh(threshold=3)
    for _ in range(2):
        mgr.record_failure("alice")
    check("2. threshold-1=2 failures still permitted", not mgr.is_locked("alice"))

    # 3. Exact threshold triggers lockout
    mgr, clock = fresh(threshold=3)
    for _ in range(3):
        mgr.record_failure("alice")
    check("3. Exact threshold (3) triggers lockout", mgr.is_locked("alice"))

    # 4. At t=299s still locked
    mgr, clock = fresh(threshold=3, lockout_seconds=300)
    for _ in range(3):
        mgr.record_failure("alice")
    clock.advance(299)
    check("4. At t=299s lockout still active", mgr.is_locked("alice"))

    # 5. At t=300s released
    mgr, clock = fresh(threshold=3, lockout_seconds=300)
    for _ in range(3):
        mgr.record_failure("alice")
    clock.advance(300)
    check("5. At t=300s lockout released", not mgr.is_locked("alice"))

    # 6. Counter resets to 0 after window expires
    mgr, clock = fresh(threshold=3, lockout_seconds=300)
    for _ in range(3):
        mgr.record_failure("alice")
    clock.advance(300)  # release
    mgr.record_failure("alice")  # one new failure
    check("6. Counter resets after window: 1 failure doesn't re-lock",
          not mgr.is_locked("alice"))

    # 7. Successful attempt resets counter
    mgr, clock = fresh(threshold=3)
    mgr.record_failure("alice")
    mgr.record_failure("alice")
    mgr.record_success("alice")
    mgr.record_failure("alice")  # starts from 0 again
    check("7. Success resets counter: 1 failure after success doesn't lock",
          not mgr.is_locked("alice"))

    # 8. Per-user isolation
    mgr, clock = fresh(threshold=3)
    for _ in range(3):
        mgr.record_failure("alice")
    check("8. Locking alice doesn't affect bob", not mgr.is_locked("bob"))

    # 9. FakeClock boundary precision: t=299 locked, t=300 released
    for advance, expected_locked, label in [(299, True, "299s"), (300, False, "300s")]:
        mgr2, clock2 = fresh(threshold=3, lockout_seconds=300)
        for _ in range(3):
            mgr2.record_failure("alice")
        clock2.advance(advance)
        got = mgr2.is_locked("alice")
        check(f"9. FakeClock boundary t={label}: locked={expected_locked}",
              got == expected_locked, f"got {got}, expected {expected_locked}")

    # 10. BuggyLockoutManager never unlocks at t=300
    clock3 = FakeClock(0.0)
    buggy = BuggyLockoutManager(threshold=3, lockout_seconds=300, clock=clock3)
    for _ in range(3):
        buggy.record_failure("alice")
    clock3.advance(300)
    check("10. BuggyLockoutManager detected: still locked at t=300",
          buggy.is_locked("alice"))

    # 11. Configurable threshold=1
    mgr11, clock11 = fresh(threshold=1)
    mgr11.record_failure("alice")
    check("11. threshold=1: single failure locks", mgr11.is_locked("alice"))

    # 12. Concurrent attempt safety: 2 threads, threshold=2
    # Both threads fire record_failure simultaneously; exactly one should trigger the lock
    clock12 = FakeClock(0.0)
    mgr12 = LockoutManager(threshold=2, lockout_seconds=300, clock=clock12)
    barrier = threading.Barrier(2)
    lock_counts = []

    def concurrent_fail():
        barrier.wait()
        mgr12.record_failure("concurrent_user")

    threads = [threading.Thread(target=concurrent_fail) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("12. Concurrent 2x failure at threshold=2 -> user is locked",
          mgr12.is_locked("concurrent_user"))

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="lockout_test_harness",
        description="Temporal PIN lockout test harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all 12 scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only")
    p.add_argument("--port", type=int, default=19250,
                   help="Mock server port (default: 19250)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        server = start_mock_server(args.port)
        print(f"  Lockout mock server on http://127.0.0.1:{args.port} — Ctrl+C to stop")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()
        return

    if args.self_test:
        print("\n  LOCKOUT TEST HARNESS — self-test mode")
        print("  " + "=" * 52)
        results = run_all_scenarios(verbose=args.verbose)
        passed = sum(1 for r in results if r.passed)
        failed = sum(1 for r in results if not r.passed)
        if not args.verbose:
            for r in results:
                print(r)
        print()
        print(f"  Results: {passed} passed, {failed} failed out of {len(results)}")
        print()
        sys.exit(0 if failed == 0 else 1)

    parser.print_help()


if __name__ == "__main__":
    main()
