#!/usr/bin/env python3
"""
<NAME>_test_harness.py — One-line purpose of this harness.
============================================================

Pure-stdlib. Zero external dependencies.

Replace this docstring with: what the harness validates, what bug class it
catches, and a one-liner self-test command. Example:

  python harnesses/<cat>/<name>_test_harness.py --self-test
  python harnesses/<cat>/<name>_test_harness.py --list-scenarios
  python harnesses/<cat>/<name>_test_harness.py --port 19999

Pattern (see CLAUDE.md):
  - @dataclass configs for tunables.
  - argparse CLI with --self-test / --list-scenarios / --port / --verbose.
  - _run_self_test() returns a process exit code.
  - Optional ThreadingHTTPServer for networked harnesses.
  - if __name__ == "__main__": sys.exit(main())
"""

from __future__ import annotations

import argparse
import http.server
import json
import socketserver
import sys
import threading
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    """Tunables for the harness. Keep this small and explicit."""

    port: int = 19999  # CHANGE: pick a free port from CLAUDE.md port table
    scenarios: list[str] = field(default_factory=list)
    verbose: bool = False


# ---------------------------------------------------------------------------
# Core engine — replace with your harness logic
# ---------------------------------------------------------------------------


def run_scenario(name: str, config: HarnessConfig) -> dict[str, Any]:
    """Run one scenario. Replace this with the real logic."""
    # CHANGE: real scenario implementation.
    return {"name": name, "ok": True}


def list_scenarios(config: HarnessConfig) -> list[str]:
    """Return the catalog of scenarios this harness can run."""
    # CHANGE: enumerate built-in scenarios.
    return ["scenario_one", "scenario_two"]


# ---------------------------------------------------------------------------
# Mock HTTP server (delete this section if the harness is in-process)
# ---------------------------------------------------------------------------


class MockHandler(http.server.BaseHTTPRequestHandler):
    """Mock handler — replace with the real fixture surface."""

    def log_message(self, format: str, *args: Any) -> None:  # silence
        pass

    def do_GET(self) -> None:
        # CHANGE: real GET handling.
        body = json.dumps({"path": self.path, "ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_mock_server(port: int) -> socketserver.TCPServer:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), MockHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _run_self_test(config: HarnessConfig) -> int:
    """Run the built-in self-test scenarios. Return 0 on success, non-zero on failure."""
    failures: list[str] = []
    for name in list_scenarios(config):
        try:
            result = run_scenario(name, config)
            if not result.get("ok"):
                failures.append(f"{name}: {result}")
        except Exception as exc:
            failures.append(f"{name}: {exc!r}")

    if failures:
        print(f"FAILED ({len(failures)}):", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print(f"OK: {len(list_scenarios(config))} scenarios passed.")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="<one-line description>",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--self-test", action="store_true", help="Run built-in self-test")
    p.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    p.add_argument("--port", type=int, default=HarnessConfig.port, help="Mock server port")
    p.add_argument("--verbose", action="store_true", help="Verbose output")
    return p


def main() -> int:
    args = build_parser().parse_args()
    config = HarnessConfig(port=args.port, verbose=args.verbose)

    if args.list_scenarios:
        for name in list_scenarios(config):
            print(name)
        return 0

    if args.self_test:
        return _run_self_test(config)

    # Default: start the mock server and block.
    server = start_mock_server(config.port)
    print(f"Mock server on http://127.0.0.1:{config.port} (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
