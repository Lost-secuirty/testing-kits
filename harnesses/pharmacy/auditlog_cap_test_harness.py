#!/usr/bin/env python3
"""auditlog_cap_test_harness.py — Rotating-Capped Audit Log Harness (2026)
=========================================================================
Pure-Python (ZERO dependencies) harness for testing a database-backed
ring-buffer audit log with a hard entry cap.

Distinct from logging_test_harness (#17) which tests log format/sensitive-data:
  - Tests the cap/prune/retain algorithm (DELETE WHERE id NOT IN (... LIMIT cap))
  - Tests ordering invariants: newest rows always retained, oldest discarded
  - Tests export completeness (full table, not just retrieval limit of 50)
  - Tests concurrent write safety (WAL + transactional prune)
  - BuggyAuditLogStore (skips DELETE) proves the harness catches cap violations

Models data.py db_log_audit() from pharmacy_app with MAX_LOG_ENTRIES=10000.

Port: 19270

Usage:
  python auditlog_cap_test_harness.py --self-test
  python auditlog_cap_test_harness.py --mock-server --port 19270
  python auditlog_cap_test_harness.py --self-test --verbose
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ============================================================
# AUDIT LOG STORE
# ============================================================

class AuditLogStore:
    """DB-backed audit log with hard cap. Thread-safe."""

    def __init__(self, conn, cap=10000):
        self.conn = conn
        self.cap = cap
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS AuditLog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user TEXT NOT NULL,
                action TEXT NOT NULL
            );
        """)
        self.conn.commit()

    def write(self, user, action):
        """Append a row, then prune to cap newest rows."""
        with self._lock:
            ts = datetime.now().isoformat(timespec="seconds")
            self.conn.execute(
                "INSERT INTO AuditLog (timestamp, user, action) VALUES (?, ?, ?)",
                (ts, user, action),
            )
            self.conn.execute(
                "DELETE FROM AuditLog WHERE id NOT IN "
                "(SELECT id FROM AuditLog ORDER BY id DESC LIMIT ?)",
                (self.cap,),
            )
            self.conn.commit()

    def read(self, text_filter="", limit=50):
        """Return rows newest-first, capped at limit."""
        with self._lock:
            if text_filter:
                like = "%" + text_filter + "%"
                rows = self.conn.execute(
                    "SELECT timestamp, user, action FROM AuditLog "
                    "WHERE user LIKE ? OR action LIKE ? ORDER BY id DESC LIMIT ?",
                    (like, like, int(limit))
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT timestamp, user, action FROM AuditLog "
                    "ORDER BY id DESC LIMIT ?",
                    (int(limit),)
                ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def count(self):
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM AuditLog").fetchone()[0]

    def max_id(self):
        with self._lock:
            row = self.conn.execute(
                "SELECT MAX(id) FROM AuditLog").fetchone()
            return row[0] if row and row[0] is not None else 0

    def export(self, path):
        """Write all rows (oldest-first) to a plain-text file. Returns path."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT timestamp, user, action FROM AuditLog ORDER BY id ASC"
            ).fetchall()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Pharmacy Audit Log Export\n")
            fh.write(f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
            fh.write(f"Total entries: {len(rows)}\n")
            fh.write("---\n")
            fh.write("timestamp\tuser\taction\n")
            for r in rows:
                fh.write(f"{r[0]}\t{r[1]}\t{r[2]}\n")
        return path


class BuggyAuditLogStore(AuditLogStore):
    """Skips the DELETE step — cap is never enforced (demonstrates detection)."""

    def write(self, user, action):
        with self._lock:
            ts = datetime.now().isoformat(timespec="seconds")
            self.conn.execute(
                "INSERT INTO AuditLog (timestamp, user, action) VALUES (?, ?, ?)",
                (ts, user, action),
            )
            # Intentionally omits DELETE (the bug)
            self.conn.commit()


def _make_conn():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    return conn


# ============================================================
# MOCK HTTP SERVER
# ============================================================

class AuditLogHandler(BaseHTTPRequestHandler):
    store = None

    def do_POST(self):
        if self.path != "/audit":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
            AuditLogHandler.store.write(req.get("user", ""), req.get("action", ""))
            self.send_response(201)
            self.end_headers()
        except Exception:
            self.send_response(400)
            self.end_headers()

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        if parsed.path != "/audit":
            self.send_response(404)
            self.end_headers()
            return
        params = parse_qs(parsed.query)
        limit = int(params.get("limit", ["50"])[0])
        rows = AuditLogHandler.store.read(limit=limit)
        resp = json.dumps([(r[0], r[1], r[2]) for r in rows]).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, fmt, *args):
        pass


def start_mock_server(port=19270):
    store = AuditLogStore(_make_conn(), cap=10000)
    AuditLogHandler.store = store
    server = ThreadingHTTPServer(("127.0.0.1", port), AuditLogHandler)
    server.daemon_threads = True
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


# ============================================================
# TEST SCENARIOS
# ============================================================

class AuditLogTestResult:
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
        r = AuditLogTestResult(name, cond, detail)
        results.append(r)
        if verbose:
            print(r)
        return cond

    # 1. Single write -> count == 1
    s1 = AuditLogStore(_make_conn(), cap=10000)
    s1.write("alice", "login")
    check("1. Single write -> count == 1", s1.count() == 1, f"got {s1.count()}")

    # 2. Rows ordered newest-first
    s2 = AuditLogStore(_make_conn(), cap=10000)
    s2.write("alice", "first")
    s2.write("alice", "second")
    rows2 = s2.read(limit=10)
    check("2. Rows ordered newest-first",
          len(rows2) == 2 and rows2[0][2] == "second",
          f"order: {[r[2] for r in rows2]}")

    # 3. cap=5: inserting 4 rows no premature pruning
    s3 = AuditLogStore(_make_conn(), cap=5)
    for i in range(4):
        s3.write("u", f"action_{i}")
    check("3. cap=5, 4 inserts: no premature pruning (count==4)",
          s3.count() == 4, f"got {s3.count()}")

    # 4. cap=5: inserting 6 rows leaves exactly 5
    s4 = AuditLogStore(_make_conn(), cap=5)
    for i in range(6):
        s4.write("u", f"action_{i}")
    check("4. cap=5, 6 inserts: count==5", s4.count() == 5, f"got {s4.count()}")

    # 5. Newest rows retained
    s5 = AuditLogStore(_make_conn(), cap=5)
    for i in range(6):
        s5.write("u", f"action_{i}")  # 0 is oldest, 5 is newest
    rows5 = s5.read(limit=10)
    actions5 = {r[2] for r in rows5}
    check("5. Newest retained: oldest action (action_0) is gone after prune",
          "action_0" not in actions5 and "action_5" in actions5,
          f"actions: {actions5}")

    # 6. Idempotent: 3x cap inserts always leaves exactly cap
    s6 = AuditLogStore(_make_conn(), cap=5)
    for i in range(15):
        s6.write("u", f"a{i}")
    check("6. 3x cap inserts: count still == cap(5)",
          s6.count() == 5, f"got {s6.count()}")

    # 7. LowCap(cap=3): 4th insert prunes to 3
    s7 = AuditLogStore(_make_conn(), cap=3)
    for i in range(4):
        s7.write("u", f"row{i}")
    check("7. LowCap(cap=3): 4 inserts -> count==3",
          s7.count() == 3, f"got {s7.count()}")

    # 8. Auto-increment ID not reset after prune
    s8 = AuditLogStore(_make_conn(), cap=3)
    for i in range(3):
        s8.write("u", f"r{i}")
    max_id_before = s8.max_id()
    s8.write("u", "trigger_prune")
    max_id_after = s8.max_id()
    check("8. Auto-increment ID not reset after prune",
          max_id_after > max_id_before,
          f"before={max_id_before}, after={max_id_after}")

    # 9. text_filter still works after prune
    s9 = AuditLogStore(_make_conn(), cap=5)
    for i in range(6):
        s9.write("u", f"login_{i}")
    s9.write("admin", "special_export")
    filtered = s9.read(text_filter="special_export", limit=10)
    check("9. text_filter works after prune",
          len(filtered) == 1 and filtered[0][2] == "special_export",
          f"got: {filtered}")

    # 10. Integration: cap=10, 15 inserts -> export has 10 rows
    tmpdir10 = tempfile.mkdtemp(prefix="auditlog_")
    try:
        s10 = AuditLogStore(_make_conn(), cap=10)
        for i in range(15):
            s10.write("user", f"act_{i}")
        export_path = os.path.join(tmpdir10, "export.txt")
        s10.export(export_path)
        with open(export_path, "r") as fh:
            lines = fh.readlines()
        # header: 4 lines + header row + 10 data rows = 16 lines total
        data_lines = [l for l in lines if l.startswith("202")]
        check("10. Integration: export has exactly cap(10) data rows",
              len(data_lines) == 10,
              f"got {len(data_lines)} data lines")
    finally:
        shutil.rmtree(tmpdir10, ignore_errors=True)

    # 11. Export header format
    tmpdir11 = tempfile.mkdtemp(prefix="auditlog_hdr_")
    try:
        s11 = AuditLogStore(_make_conn(), cap=100)
        s11.write("alice", "login")
        ep = os.path.join(tmpdir11, "exp.txt")
        s11.export(ep)
        with open(ep, "r") as fh:
            content = fh.read()
        check("11. Export header: starts with 'Pharmacy Audit Log Export'",
              content.startswith("Pharmacy Audit Log Export"),
              f"first line: {content.splitlines()[0]!r}")
        check("11b. Export header: contains 'Generated:'",
              "Generated:" in content)
        check("11c. Export header: contains '---'", "---" in content)
    finally:
        shutil.rmtree(tmpdir11, ignore_errors=True)

    # 12. Concurrent writes: 4 threads x 3 writes each, cap=5
    s12 = AuditLogStore(_make_conn(), cap=5)
    barrier12 = threading.Barrier(4)
    errors12 = []

    def writer(tid):
        barrier12.wait()
        try:
            for i in range(3):
                s12.write(f"t{tid}", f"action_{i}")
        except Exception as e:
            errors12.append(str(e))

    threads12 = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
    for t in threads12:
        t.start()
    for t in threads12:
        t.join()
    c12 = s12.count()
    check("12. Concurrent writes: no exceptions raised",
          errors12 == [], f"errors: {errors12}")
    check("12b. Concurrent writes: count <= cap(5)",
          c12 <= 5, f"got count={c12}")

    # 13. BuggyAuditLogStore detected
    bs13 = BuggyAuditLogStore(_make_conn(), cap=5)
    for i in range(6):
        bs13.write("u", f"b{i}")
    check("13. BuggyAuditLogStore detected: count > cap",
          bs13.count() > 5, f"got {bs13.count()}")

    return results


# ============================================================
# CLI
# ============================================================

def build_parser():
    p = argparse.ArgumentParser(
        prog="auditlog_cap_test_harness",
        description="Rotating-capped audit log test harness (pure stdlib)",
    )
    p.add_argument("--self-test", action="store_true",
                   help="Run all 13 scenarios and exit 0 if all pass")
    p.add_argument("--mock-server", action="store_true",
                   help="Start mock HTTP server only")
    p.add_argument("--port", type=int, default=19270,
                   help="Mock server port (default: 19270)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


def main():
    import time as _time
    parser = build_parser()
    args = parser.parse_args()

    if args.mock_server:
        server = start_mock_server(args.port)
        print(f"  Audit log mock server on http://127.0.0.1:{args.port} — Ctrl+C to stop")
        try:
            while True:
                _time.sleep(1)
        except KeyboardInterrupt:
            server.shutdown()
            server.server_close()
        return

    if args.self_test:
        print("\n  AUDIT LOG CAP TEST HARNESS — self-test mode")
        print("  " + "=" * 54)
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
