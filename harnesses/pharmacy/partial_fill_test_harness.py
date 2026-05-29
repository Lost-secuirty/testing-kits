#!/usr/bin/env python3
"""
Harness 43 — Partial-Fill Two-Phase Ledger Test Harness (Port 19290)

Tests the open→resolve lifecycle for pharmacy partial dispensing:
- True/False return contract on resolve
- Exactly-once concurrent resolution
- Open/resolved filtering
- Audit event on True return only

Pure Python stdlib only. No import of pharmacy_app.
"""
import argparse
import json
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# In-memory PartialFill store
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS PartialFills (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    drug     TEXT    NOT NULL,
    qty_owed INTEGER NOT NULL,
    patient  TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0
)
"""


class PartialFillStore:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute(_SCHEMA)
            self._conn.commit()

    def add(self, drug, qty_owed, patient, date):
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO PartialFills (drug, qty_owed, patient, date) VALUES (?,?,?,?)",
                (drug, qty_owed, patient, date),
            )
            self._conn.commit()
            return cur.lastrowid

    def list_open(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM PartialFills WHERE resolved=0 ORDER BY id DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def count_open(self):
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM PartialFills WHERE resolved=0"
            ).fetchone()[0]

    def resolve(self, pid):
        with self._lock:
            cur = self._conn.execute(
                "UPDATE PartialFills SET resolved=1 WHERE id=? AND resolved=0",
                (pid,),
            )
            self._conn.commit()
            return cur.rowcount > 0


class BuggyPartialFillStore(PartialFillStore):
    """Always returns True from resolve — proves harness catches 'never idempotent'."""

    def resolve(self, pid):
        with self._lock:
            self._conn.execute(
                "UPDATE PartialFills SET resolved=1 WHERE id=?", (pid,)
            )
            self._conn.commit()
            return True  # Bug: ignores rowcount


class BuggyPartialFillStore2(PartialFillStore):
    """list_open returns ALL rows including resolved — proves harness catches filter bug."""

    def list_open(self):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM PartialFills ORDER BY id DESC"  # Bug: missing WHERE resolved=0
            ).fetchall()
            return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit capture helper
# ---------------------------------------------------------------------------


class AuditCapture:
    def __init__(self):
        self._events = []
        self._lock = threading.Lock()

    def log(self, msg):
        with self._lock:
            self._events.append(msg)

    def count(self):
        with self._lock:
            return len(self._events)


# ---------------------------------------------------------------------------
# Mock HTTP server
# ---------------------------------------------------------------------------

_store = None
_audit = None


class PartialFillHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence access log
        pass

    def _send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if path == "/partials":
            pid = _store.add(
                body.get("drug", "Drug"),
                body.get("qty_owed", 1),
                body.get("patient", "Patient"),
                body.get("date", "2026-01-01"),
            )
            self._send_json(201, {"id": pid})
        elif path.startswith("/partials/") and path.endswith("/resolve"):
            parts = path.strip("/").split("/")
            try:
                pid = int(parts[1])
            except (IndexError, ValueError):
                self._send_json(400, {"error": "bad id"})
                return
            ok = _store.resolve(pid)
            if ok:
                _audit.log(f"resolved:{pid}")
                self._send_json(200, {"resolved": True})
            else:
                self._send_json(409, {"resolved": False})
        else:
            self._send_json(404, {"error": "not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/partials":
            self._send_json(
                200,
                {"open": _store.list_open(), "count": _store.count_open()},
            )
        else:
            self._send_json(404, {"error": "not found"})


def start_mock_server(port):
    global _store, _audit
    _store = PartialFillStore()
    _audit = AuditCapture()
    srv = ThreadingHTTPServer(("127.0.0.1", port), PartialFillHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


# ---------------------------------------------------------------------------
# Self-test scenarios
# ---------------------------------------------------------------------------


def _sc(label, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {label}"
    if not passed and detail:
        msg += f" — {detail}"
    print(msg)
    return passed


def run_all_scenarios(verbose=False):
    results = []

    # S01 — open partial appears with correct fields
    s = PartialFillStore()
    pid = s.add("Amoxicillin", 30, "Alice", "2026-05-25")
    opens = s.list_open()
    ok = (
        len(opens) == 1
        and opens[0]["drug"] == "Amoxicillin"
        and opens[0]["qty_owed"] == 30
        and opens[0]["patient"] == "Alice"
        and opens[0]["resolved"] == 0
    )
    results.append(_sc("S01: open partial appears with all fields correct", ok))

    # S02 — resolved partial disappears from list_open
    s = PartialFillStore()
    pid = s.add("Metformin", 10, "Bob", "2026-05-25")
    s.resolve(pid)
    results.append(_sc("S02: resolved partial disappears from list_open", len(s.list_open()) == 0))

    # S03 — resolve returns True on first call
    s = PartialFillStore()
    pid = s.add("Lisinopril", 5, "Carol", "2026-05-25")
    results.append(_sc("S03: resolve() returns True on first call", s.resolve(pid) is True))

    # S04 — resolve returns False on second call (idempotent)
    s = PartialFillStore()
    pid = s.add("Atorvastatin", 20, "Dave", "2026-05-25")
    s.resolve(pid)
    results.append(_sc("S04: resolve() returns False on second call (idempotent)", s.resolve(pid) is False))

    # S05 — resolve nonexistent ID returns False
    s = PartialFillStore()
    results.append(_sc("S05: resolve(nonexistent) returns False", s.resolve(99999) is False))

    # S06 — count_open == 5 after 5 adds
    s = PartialFillStore()
    for i in range(5):
        s.add(f"Drug{i}", i + 1, f"P{i}", "2026-05-25")
    results.append(_sc("S06: 5 open partials → count_open() == 5", s.count_open() == 5))

    # S07 — 3 open + 2 resolved → count == 3
    s = PartialFillStore()
    pids = [s.add(f"Drug{i}", 1, f"P{i}", "2026-05-25") for i in range(5)]
    s.resolve(pids[0])
    s.resolve(pids[1])
    results.append(_sc("S07: 3 open + 2 resolved → count_open() == 3", s.count_open() == 3))

    # S08 — list_open returns newest-first
    s = PartialFillStore()
    p1 = s.add("DrugA", 1, "PA", "2026-05-25")
    p2 = s.add("DrugB", 1, "PB", "2026-05-25")
    p3 = s.add("DrugC", 1, "PC", "2026-05-25")
    ids = [r["id"] for r in s.list_open()]
    results.append(_sc("S08: list_open returns newest-first", ids == [p3, p2, p1]))

    # S09 — resolving #2 of 3 leaves #1 and #3 open
    s = PartialFillStore()
    pa = s.add("Drug1", 1, "A", "2026-05-25")
    pb = s.add("Drug2", 1, "B", "2026-05-25")
    pc = s.add("Drug3", 1, "C", "2026-05-25")
    s.resolve(pb)
    open_ids = {r["id"] for r in s.list_open()}
    results.append(_sc("S09: resolving #2 of 3 leaves #1 and #3 open", open_ids == {pa, pc}))

    # S10 — AuditCapture logs only on True return
    s = PartialFillStore()
    audit = AuditCapture()
    pid = s.add("Drug", 1, "P", "2026-05-25")
    if s.resolve(pid):
        audit.log(f"resolved:{pid}")
    if s.resolve(pid):
        audit.log(f"resolved:{pid}")
    results.append(_sc("S10: AuditCapture logs exactly once for True return", audit.count() == 1))

    # S11 — qty_owed=99 faithfully persisted
    s = PartialFillStore()
    s.add("Insulin", 99, "Patient", "2026-05-25")
    results.append(_sc("S11: qty_owed=99 persisted and retrieved correctly", s.list_open()[0]["qty_owed"] == 99))

    # S12 — concurrent resolve race: exactly one True
    s = PartialFillStore()
    pid = s.add("RaceDrug", 1, "RaceP", "2026-05-25")
    race_results = []
    barrier = threading.Barrier(2)

    def _race():
        barrier.wait()
        race_results.append(s.resolve(pid))

    t1, t2 = threading.Thread(target=_race), threading.Thread(target=_race)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    ok = (
        sum(1 for r in race_results if r is True) == 1
        and sum(1 for r in race_results if r is False) == 1
    )
    results.append(_sc("S12: concurrent resolve → exactly one True, one False", ok))

    # S-B1 — BuggyPartialFillStore (always-True) detected
    b1 = BuggyPartialFillStore()
    pid = b1.add("Drug", 1, "P", "2026-05-25")
    b1.resolve(pid)
    results.append(
        _sc("S-B1: BuggyStore (always-True) detected on 2nd resolve", b1.resolve(pid) is True)
    )

    # S-B2 — BuggyPartialFillStore2 (no WHERE filter) detected
    b2 = BuggyPartialFillStore2()
    pid = b2.add("Drug", 1, "P", "2026-05-25")
    b2.resolve(pid)
    results.append(
        _sc("S-B2: BuggyStore2 (no filter) leaks resolved row into list_open", len(b2.list_open()) > 0)
    )

    total = len(results)
    passed = sum(results)
    print(f"\n  {passed}/{total} scenarios passed")
    return passed == total


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def build_parser():
    p = argparse.ArgumentParser(
        description="Harness 43 — Partial-Fill Two-Phase Ledger (Port 19290)"
    )
    p.add_argument("--port", type=int, default=19290)
    p.add_argument("--self-test", action="store_true", help="Run built-in scenarios and exit")
    p.add_argument("--verbose", action="store_true")
    return p


def main():
    try:  # Windows cp1252 console chokes on → in scenario output
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args()
    if args.self_test:
        print("Harness 43 — Partial-Fill Two-Phase Ledger")
        print("=" * 50)
        ok = run_all_scenarios(verbose=args.verbose)
        raise SystemExit(0 if ok else 1)
    srv = start_mock_server(args.port)
    print(f"Harness 43 partial-fill mock server listening on :{args.port}")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
