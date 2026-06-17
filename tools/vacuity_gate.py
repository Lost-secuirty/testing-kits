#!/usr/bin/env python3
"""Vacuous-green meta-gate — neuter each harness's oracle and prove its self-test goes red.

A harness is "vacuous green" if its own self-test/proof can pass while the thing it tests is
broken. This gate makes that machine-checkable: for every lib/ai harness that declares a
``VACUITY_TARGETS`` list, it replaces each oracle symbol with a TYPE-FAITHFUL wrong value of its
real return (a flipped bool, off-by-one int, empty-but-valid container — ADR-0007 D1), re-runs the
harness's ``run_self_test()`` in a subprocess, and asserts the self-test now FAILS. Because the
wrong value is the right TYPE, a self-test that genuinely asserts the oracle's output fails at its
ASSERTION (not a type-crash) — so the gate measures oracle STRENGTH, not just reachability. If the
self-test still passes, the harness has no teeth (its self-test never checked the oracle's output).

This delivers the per-harness mutation runner ADR-0006 deferred, WITHOUT mutmut (which can't run
on native Windows and chokes on package-prefixed modules): a direct monkeypatch in a subprocess
sidesteps both. Integration harnesses are excluded — their self-tests defer to Docker, exactly
as ``make selftest`` excludes them.

Status semantics (per mapped harness):
  TEETH    control self-test is green AND the neutered self-test is red  -> good
  VACUOUS  control green but neutered self-test STILL green               -> blocking failure
  ERROR    control self-test is already red (broken harness)              -> blocking failure
  UNMAPPED no VACUITY_TARGETS declared                                    -> advisory only (rollout)

Usage:
  python tools/vacuity_gate.py            # gate all lib+ai harnesses
  python tools/vacuity_gate.py --self-test  # prove the gate itself on two fixtures
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib
import importlib.util
import io
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # bootstrap so `import harnesses` works under bare python
    sys.path.insert(0, str(ROOT))
FLAVORS = ("core", "security", "ai")  # pharmacy is legacy (older soft gate); excluded

_SENTINEL = object()


def _mutate(value):
    """Return a plausible-but-WRONG value of the SAME type as ``value`` (ADR-0007 D1). The point:
    a self-test that genuinely asserts the oracle's output then goes red via its ASSERTION, not via
    a type-crash on a foreign sentinel — turning the gate from a reachability check into an
    oracle-strength check. Custom types (and ``None``) have no type-faithful wrong value, so they
    fall back to ``_SENTINEL`` (a documented limitation: those reds remain reachability-only)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1.0
    if isinstance(value, bytes):
        return value + b"\x00"
    if isinstance(value, str):
        return value + "_MUT"
    if isinstance(value, frozenset):
        return frozenset()
    if isinstance(value, set):
        return set()
    if isinstance(value, dict):
        return {}
    if isinstance(value, (list, tuple)):
        return type(value)()
    return _SENTINEL  # None / custom objects: no type-faithful wrong value


def _mutating(func):
    """Wrap ``func`` so the real oracle still runs but its return is mutated to a wrong value of
    the same type. Preserves async-ness so a coroutine oracle stays awaitable."""
    if asyncio.iscoroutinefunction(func):
        async def awrapper(*a, **k):
            return _mutate(await func(*a, **k))

        return awrapper

    def wrapper(*a, **k):
        return _mutate(func(*a, **k))

    return wrapper


def _neuter(module, dotted: str) -> None:
    """Replace ``dotted`` (``func`` or ``Class.method``) with a type-mutating wrapper of the REAL
    symbol (ADR-0007 D1): the oracle runs, but its result is mutated wrong, so the self-test must
    fail at its assertion. A method wrapper takes ``self`` as its first positional like any method."""
    parts = dotted.split(".")
    if len(parts) == 1:
        setattr(module, parts[0], _mutating(getattr(module, parts[0])))
    elif len(parts) == 2:
        cls = getattr(module, parts[0])
        setattr(cls, parts[1], _mutating(getattr(cls, parts[1])))
    else:
        raise ValueError(f"unsupported VACUITY_TARGET {dotted!r} (expected 'func' or 'Class.method')")


def _load(module_ref: str):
    """Import a dotted module, or load a fixture by .py path."""
    if module_ref.endswith(".py"):
        spec = importlib.util.spec_from_file_location("_vacuity_fixture", module_ref)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return importlib.import_module(module_ref)


def _worker(module_ref: str, targets: list[str]) -> int:
    """Neuter ``targets`` on the module, then return its ``run_self_test()`` exit code. An
    uncaught exception from a neutered oracle propagates and exits non-zero — also a red."""
    module = _load(module_ref)
    for target in targets:
        _neuter(module, target)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = module._run_self_test()
    # A self-test returns 0 (green) / 1 (red). Coerce a bare ``None`` (a function that
    # falls off the end) to 0 rather than crashing the worker — int(None) would TypeError
    # and a green control harness would be misread as ERROR during the VACUITY_TARGETS rollout.
    return 0 if result is None else int(result)


def _run_worker(module_ref: str, targets: list[str]) -> int:
    """Run the worker in a fresh subprocess (isolation) and return its exit code."""
    args = [sys.executable, str(Path(__file__)), "--worker", "--module", module_ref]
    if targets:
        args += ["--targets", ",".join(targets)]
    return subprocess.run(args, capture_output=True, text=True).returncode


def _classify(module_ref: str, targets: list[str]) -> str:
    # SOUNDNESS NOTE (ADR-0007 D1): the stand-in now returns a TYPE-FAITHFUL wrong value, so for an
    # oracle returning a primitive/container the neutered red comes from the self-test's ASSERTION —
    # the gate measures oracle strength, not mere reachability. Residual limitation: an oracle whose
    # return is a CUSTOM type or None (no type-faithful wrong value) falls back to a sentinel, so its
    # red can still be a type-crash (reachability-only) — and an oracle whose contract is raise-or-not
    # rather than return-a-value is unaffected by return mutation (it will read VACUOUS unless the
    # self-test also asserts the returned value, which is the intended pressure to strengthen it).
    control = _run_worker(module_ref, [])          # no neuter — harness should be green
    if control != 0:
        return "ERROR"                              # self-test already red without us
    neutered = _run_worker(module_ref, targets)
    return "TEETH" if neutered != 0 else "VACUOUS"


def _discover() -> list[tuple[str, list[str] | None]]:
    found = []
    for flavor in FLAVORS:
        for path in sorted((ROOT / "harnesses" / flavor).glob("*_test_harness.py")):
            module_ref = f"harnesses.{flavor}.{path.stem}"
            targets = getattr(importlib.import_module(module_ref), "VACUITY_TARGETS", None)
            found.append((module_ref, targets))
    return found


def run_gate() -> int:
    mapped_bad, errors, unmapped, teeth = [], [], [], []
    for module_ref, targets in _discover():
        if not targets:
            unmapped.append(module_ref)
            print(f"  UNMAPPED  {module_ref}  (no VACUITY_TARGETS — advisory)")
            continue
        status = _classify(module_ref, list(targets))
        print(f"  {status:<8}  {module_ref}  targets={list(targets)}")
        if status == "TEETH":
            teeth.append(module_ref)
        elif status == "VACUOUS":
            mapped_bad.append(module_ref)
        else:
            errors.append(module_ref)

    print(
        f"\nvacuity-gate: {len(teeth)} teeth, {len(mapped_bad)} vacuous, "
        f"{len(errors)} error, {len(unmapped)} unmapped (advisory)."
    )
    if mapped_bad or errors:
        print("FAIL: a mapped harness stayed green with its oracle neutered (vacuous), "
              "or its self-test was already red.", file=sys.stderr)
        return 1
    return 0


def _run_self_test() -> int:
    """Prove the gate itself on two fixtures: a real harness must read TEETH; a deliberately
    vacuous one must read VACUOUS (else the gate has no teeth)."""
    fx = ROOT / "tools" / "_vacuity_fixtures"
    real = str(fx / "real_harness.py")
    vacuous = str(fx / "vacuous_harness.py")
    failures = 0

    real_status = _classify(real, ["oracle"])
    if real_status != "TEETH":
        failures += 1
        print(f"FAIL: real fixture classified {real_status}, expected TEETH", file=sys.stderr)

    vacuous_status = _classify(vacuous, ["oracle"])
    if vacuous_status != "VACUOUS":
        failures += 1
        print(f"FAIL: vacuous fixture classified {vacuous_status}, expected VACUOUS "
              "(the gate did not detect a vacuous harness)", file=sys.stderr)

    if failures:
        print(f"self-test: {failures} failure(s)", file=sys.stderr)
        return 1
    print("self-test: OK (real fixture reads TEETH; vacuous fixture is detected as VACUOUS)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vacuous-green meta-gate")
    parser.add_argument("--self-test", action="store_true", help="prove the gate on its fixtures")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--module", help=argparse.SUPPRESS)
    parser.add_argument("--targets", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        targets = [t for t in args.targets.split(",") if t]
        return _worker(args.module, targets)
    if args.self_test:
        return _run_self_test()
    print("Vacuous-green meta-gate — neutering each mapped harness's oracle:\n")
    return run_gate()


if __name__ == "__main__":
    sys.exit(main())
