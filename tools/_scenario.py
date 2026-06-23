#!/usr/bin/env python3
"""Shared self-test scaffolding + CLI for the stdlib reporting tools.

`owasp_coverage.py` and `findings_export.py` both expose a `--self-test` that runs a
list of named scenarios and a `--list-scenarios` flag. This module holds the common
`ScenarioResult` shape, the result collector, the report printer, and a small CLI
dispatcher so that boilerplate lives in one place (pure-stdlib, zero dependencies).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        msg = f"  [{status}] {self.name}"
        if not self.passed and self.detail:
            msg += f"\n      {self.detail}"
        return msg


class ScenarioRun:
    """Collects scenario checks; prints each as it runs when verbose."""

    def __init__(self, verbose: bool = False) -> None:
        self.results: list[ScenarioResult] = []
        self._verbose = verbose

    def check(self, name: str, cond: bool, detail: str = "") -> None:
        result = ScenarioResult(name, bool(cond), detail)
        self.results.append(result)
        if self._verbose:
            print(result)


def report(title: str, results: list[ScenarioResult], verbose: bool = False) -> int:
    """Print a self-test report; return 0 if all scenarios passed, else 1."""
    print(f"\n  {title}")
    print("  " + "=" * 52)
    if not verbose:
        for result in results:
            print(result)
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    print(f"\n  Results: {passed} passed, {failed} failed out of {len(results)}\n")
    return 0 if failed == 0 else 1


def selftest_cli(
    prog: str,
    description: str,
    title: str,
    scenarios: Callable[[bool], list[ScenarioResult]],
    default_action: Callable[[], int] | None = None,
    extra: dict[str, tuple[str, Callable[[], int]]] | None = None,
) -> int:
    """Standard `--self-test` / `--list-scenarios` / `--verbose` CLI.

    `scenarios(verbose)` returns the scenario results. `default_action` runs when no
    flag is given (defaults to printing help). `extra` maps a flag name (no leading
    `--`) to ``(help_text, action)`` for tool-specific modes such as ``--demo``.
    """
    parser = argparse.ArgumentParser(
        prog=prog, description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--self-test", action="store_true",
                        help="Run all scenarios; exit 0 if all pass")
    parser.add_argument("--list-scenarios", action="store_true", help="List built-in scenarios")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    extra = extra or {}
    for flag, (help_text, _action) in extra.items():
        parser.add_argument(f"--{flag}", action="store_true", help=help_text)
    args = parser.parse_args()

    for flag, (_help_text, action) in extra.items():
        if getattr(args, flag.replace("-", "_")):
            return action()
    if args.list_scenarios:
        for result in scenarios(False):
            print(result.name)
        return 0
    if args.self_test:
        return report(title, scenarios(args.verbose), args.verbose)
    if default_action is not None:
        return default_action()
    parser.print_help()
    return 0
