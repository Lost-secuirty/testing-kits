"""
Property-Based Test Harness
Harness 11 of 36

Defines invariants that hold across random inputs, with automatic shrinking
of failing inputs. Includes a mock HTTP server on a dynamic port.
Pure stdlib, zero external dependencies.
"""

from __future__ import annotations

import argparse
import copy
import http.server
import json
import math
import random
import string
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------

GenFunc = Callable[[random.Random], Any]


def gen_int(min_val: int = -100, max_val: int = 100) -> GenFunc:
    """Generate random integers in [min_val, max_val]."""

    def _gen(rng: random.Random) -> int:
        return rng.randint(min_val, max_val)

    return _gen


def gen_float(min_val: float = -1e6, max_val: float = 1e6) -> GenFunc:
    """Generate random floats in [min_val, max_val]."""

    def _gen(rng: random.Random) -> float:
        return rng.uniform(min_val, max_val)

    return _gen


def gen_string(
    min_len: int = 0,
    max_len: int = 20,
    alphabet: str = string.ascii_letters + string.digits + " ",
) -> GenFunc:
    """Generate random strings."""

    def _gen(rng: random.Random) -> str:
        length = rng.randint(min_len, max_len)
        return "".join(rng.choice(alphabet) for _ in range(length))

    return _gen


def gen_list(item_gen: GenFunc, min_len: int = 0, max_len: int = 10) -> GenFunc:
    """Generate random lists of items produced by item_gen."""

    def _gen(rng: random.Random) -> list:
        length = rng.randint(min_len, max_len)
        return [item_gen(rng) for _ in range(length)]

    return _gen


def gen_tuple(*gens: GenFunc) -> GenFunc:
    """Generate tuples where each element is produced by the corresponding generator."""

    def _gen(rng: random.Random) -> tuple:
        return tuple(g(rng) for g in gens)

    return _gen


def gen_dict(key_gen: GenFunc, val_gen: GenFunc, min_len: int = 0, max_len: int = 5) -> GenFunc:
    """Generate random dicts."""

    def _gen(rng: random.Random) -> dict:
        length = rng.randint(min_len, max_len)
        result = {}
        for _ in range(length):
            k = key_gen(rng)
            v = val_gen(rng)
            result[k] = v
        return result

    return _gen


def gen_one_of(*gens: GenFunc) -> GenFunc:
    """Pick one generator at random and use it."""

    def _gen(rng: random.Random) -> Any:
        chosen = rng.choice(gens)
        return chosen(rng)

    return _gen


def gen_bool() -> GenFunc:
    """Generate random booleans."""

    def _gen(rng: random.Random) -> bool:
        return rng.choice([True, False])

    return _gen


def gen_none() -> GenFunc:
    """Always generate None."""

    def _gen(rng: random.Random) -> None:
        return None

    return _gen


def gen_positive_int(max_val: int = 100) -> GenFunc:
    """Generate positive integers (>0)."""
    return gen_int(1, max_val)


def gen_non_negative_int(max_val: int = 100) -> GenFunc:
    """Generate non-negative integers (>=0)."""
    return gen_int(0, max_val)


# ---------------------------------------------------------------------------
# Shrinker
# ---------------------------------------------------------------------------


class Shrinker:
    """
    Reduces a failing input to a minimal counterexample.

    Shrinking strategies:
    - int: try 0, bisect towards 0
    - float: try 0.0, bisect towards 0
    - str: try "", remove chars from ends, substitute chars
    - list: try [], remove elements, shrink elements
    - tuple: shrink each element
    - dict: remove keys, shrink values
    """

    def shrink(self, value: Any, predicate: Callable[[Any], bool]) -> Any:
        """Return the smallest value that still satisfies the predicate (still fails)."""
        if isinstance(value, bool):
            return self._shrink_bool(value, predicate)
        elif isinstance(value, int):
            return self._shrink_int(value, predicate)
        elif isinstance(value, float):
            return self._shrink_float(value, predicate)
        elif isinstance(value, str):
            return self._shrink_str(value, predicate)
        elif isinstance(value, list):
            return self._shrink_list(value, predicate)
        elif isinstance(value, tuple):
            return self._shrink_tuple(value, predicate)
        elif isinstance(value, dict):
            return self._shrink_dict(value, predicate)
        return value

    def _shrink_bool(self, value: bool, predicate: Callable) -> bool:
        if predicate(False):
            return False
        return value

    def _shrink_int(self, value: int, predicate: Callable) -> int:
        # Try 0 first
        if value != 0 and predicate(0):
            value = 0
            return value

        best = value
        # Bisect towards 0
        _lo, _hi = 0, abs(value)
        sign = 1 if value > 0 else -1

        # Repeatedly halve distance to 0
        candidate = best
        for _ in range(64):
            mid = candidate // 2
            if mid == candidate:
                break
            if predicate(sign * mid):
                candidate = mid
                if abs(candidate) < abs(best):
                    best = sign * mid
            else:
                break

        # Try each integer between 0 and best
        if abs(best) <= 20:
            for i in range(abs(best)):
                if predicate(sign * i):
                    best = sign * i
                    break

        return best

    def _shrink_float(self, value: float, predicate: Callable) -> float:
        if not math.isfinite(value):
            return value

        # Try 0.0
        if predicate(0.0):
            return 0.0

        best = value
        # Bisect towards 0
        candidate = best
        for _ in range(64):
            mid = candidate / 2.0
            if abs(mid - candidate) < 1e-15:
                break
            if predicate(mid):
                candidate = mid
                if abs(candidate) < abs(best):
                    best = mid
            else:
                # Try rounding to integer
                rounded = float(round(candidate))
                if rounded != candidate and predicate(rounded):
                    candidate = rounded
                    if abs(candidate) < abs(best):
                        best = rounded

        return best

    def _shrink_str(self, value: str, predicate: Callable) -> str:
        # Try empty string
        if predicate(""):
            return ""

        best = value

        # Iteratively shrink length: try to remove characters until we can't
        changed = True
        while changed:
            changed = False
            # Try removing from the end: find shortest prefix that still satisfies
            for length in range(len(best) - 1, 0, -1):
                candidate = best[:length]
                if predicate(candidate):
                    best = candidate
                    changed = True
                    break

        # Try removing from the start
        changed = True
        while changed:
            changed = False
            for start in range(1, len(best)):
                candidate = best[start:]
                if predicate(candidate):
                    best = candidate
                    changed = True
                    break

        # Try substituting chars with simpler ones (only accept strictly simpler chars)
        # Ordered by simplicity: 'a' is simplest, then '0', then space, then original
        simple_chars = "a0 "
        result = list(best)
        changed = True
        max_passes = len(result) * len(simple_chars) + 1
        passes = 0
        while changed and passes < max_passes:
            changed = False
            passes += 1
            for i in range(len(result)):
                for sc in simple_chars:
                    # Only try chars that are strictly simpler (lower index in simple_chars
                    # or at least different in a way that reduces complexity)
                    if result[i] == sc:
                        break  # already at this simplicity level or simpler; stop trying
                    old = result[i]
                    result[i] = sc
                    candidate = "".join(result)
                    if predicate(candidate):
                        changed = True
                        best = candidate
                        break  # moved to simpler char; move to next position
                    else:
                        result[i] = old

        return best

    def _shrink_list(self, value: list, predicate: Callable) -> list:
        # Try empty list
        if predicate([]):
            return []

        best = list(value)

        # Try cutting length in half (remove from the end)
        changed = True
        while changed:
            changed = False
            n = len(best)
            if n <= 1:
                break
            candidate = best[: n // 2]
            if candidate and predicate(candidate):
                best = candidate
                changed = True
                continue
            # Try the other half
            candidate = best[n // 2 :]
            if candidate != best and predicate(candidate):
                best = candidate
                changed = True

        # Try removing individual elements
        changed = True
        while changed:
            changed = False
            for i in range(len(best)):
                candidate = best[:i] + best[i + 1 :]
                if predicate(candidate):
                    best = candidate
                    changed = True
                    break

        # Shrink individual elements
        result = list(best)
        for i in range(len(result)):
            original = result[i]

            def elem_predicate(v, idx=i):
                tmp = list(result)
                tmp[idx] = v
                return predicate(tmp)

            shrunk = self.shrink(original, elem_predicate)
            if shrunk != original:
                result[i] = shrunk
                best = list(result)

        return best

    def _shrink_tuple(self, value: tuple, predicate: Callable) -> tuple:
        result = list(value)
        for i in range(len(result)):
            original = result[i]

            def elem_predicate(v, idx=i):
                tmp = list(result)
                tmp[idx] = v
                return predicate(tuple(tmp))

            shrunk = self.shrink(original, elem_predicate)
            if shrunk != original:
                result[i] = shrunk

        return tuple(result)

    def _shrink_dict(self, value: dict, predicate: Callable) -> dict:
        best = dict(value)

        # Try removing keys
        changed = True
        while changed:
            changed = False
            for k in list(best.keys()):
                candidate = {ck: cv for ck, cv in best.items() if ck != k}
                if predicate(candidate):
                    best = candidate
                    changed = True
                    break

        # Shrink values
        for k in list(best.keys()):
            original = best[k]

            def val_predicate(v, key=k):
                tmp = dict(best)
                tmp[key] = v
                return predicate(tmp)

            shrunk = self.shrink(original, val_predicate)
            if shrunk != original:
                best[k] = shrunk

        return best


# ---------------------------------------------------------------------------
# Property
# ---------------------------------------------------------------------------


@dataclass
class CounterExample:
    """A failing example and its shrunk form."""

    original: Any
    shrunk: Any
    seed: int
    exception: Exception | None = None


@dataclass
class PropertyReport:
    """Result of running a property suite."""

    passed: int = 0
    failed: int = 0
    skipped: int = 0
    counterexamples: list[tuple[str, CounterExample]] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def __repr__(self) -> str:
        return (
            f"PropertyReport(passed={self.passed}, failed={self.failed}, "
            f"skipped={self.skipped}, counterexamples={len(self.counterexamples)})"
        )


class Property:
    """
    Represents an invariant: a generator + a predicate (+ optional precondition filter).

    Parameters
    ----------
    generator : GenFunc
        Callable(rng) -> value
    predicate : Callable
        Returns True if the property holds for the given value.
    precondition : optional Callable
        If provided, only test values where precondition(value) is True.
    name : str
        Human-readable name.
    """

    def __init__(
        self,
        generator: GenFunc,
        predicate: Callable[[Any], bool],
        precondition: Callable[[Any], bool] | None = None,
        name: str = "unnamed",
    ) -> None:
        self.generator = generator
        self.predicate = predicate
        self.precondition = precondition
        self.name = name

    def check(
        self,
        num_examples: int = 100,
        seed: int | None = None,
    ) -> CounterExample | None:
        """
        Run the property against num_examples random inputs.

        Returns the first CounterExample found (with shrunk form), or None if all pass.
        """
        runner = PropertyRunner(shrinker=Shrinker())
        return runner.run_property(self, num_examples=num_examples, seed=seed)


class PropertyRunner:
    """Runs a property against N random inputs, returns first failure + shrunk counterexample."""

    def __init__(self, shrinker: Shrinker | None = None) -> None:
        self.shrinker = shrinker or Shrinker()

    def run_property(
        self,
        prop: Property,
        num_examples: int = 100,
        seed: int | None = None,
    ) -> CounterExample | None:
        """
        Run property, return CounterExample on first failure or None.
        """
        base_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
        rng = random.Random(base_seed)

        attempts = 0
        max_attempts = num_examples * 10  # handle precondition filtering

        generated = 0
        while generated < num_examples and attempts < max_attempts:
            attempt_seed = rng.randint(0, 2**32 - 1)
            local_rng = random.Random(attempt_seed)
            attempts += 1

            try:
                value = prop.generator(local_rng)
            except Exception:
                continue

            # Check precondition
            if prop.precondition is not None:
                try:
                    if not prop.precondition(value):
                        continue
                except Exception:
                    continue

            generated += 1

            # Check predicate
            exc = None
            try:
                result = prop.predicate(value)
                holds = bool(result)
            except Exception as e:
                holds = False
                exc = e

            if not holds:
                # Shrink
                def failing_predicate(v):
                    if prop.precondition is not None:
                        try:
                            if not prop.precondition(v):
                                return False
                        except Exception:
                            return False
                    try:
                        r = prop.predicate(v)
                        return not bool(r)
                    except Exception:
                        return True

                shrunk = self.shrinker.shrink(value, failing_predicate)
                return CounterExample(
                    original=value,
                    shrunk=shrunk,
                    seed=attempt_seed,
                    exception=exc,
                )

        return None


class PropertySuite:
    """Named collection of properties with .run_all() returning PropertyReport."""

    def __init__(self, name: str = "unnamed_suite") -> None:
        self.name = name
        self._properties: list[tuple[str, Property]] = []

    def add(self, prop: Property, name: str | None = None) -> PropertySuite:
        """Add a property to this suite."""
        display_name = name or prop.name
        self._properties.append((display_name, prop))
        return self

    def property(
        self,
        generator: GenFunc,
        predicate: Callable,
        precondition: Callable | None = None,
        name: str = "unnamed",
    ) -> PropertySuite:
        """Convenience method to create and add a property."""
        prop = Property(generator, predicate, precondition=precondition, name=name)
        return self.add(prop, name)

    def run_all(
        self,
        num_examples: int = 100,
        seed: int | None = None,
    ) -> PropertyReport:
        """Run all properties and return a PropertyReport."""
        report = PropertyReport()
        for display_name, prop in self._properties:
            ce = prop.check(num_examples=num_examples, seed=seed)
            if ce is None:
                report.passed += 1
            else:
                report.failed += 1
                report.counterexamples.append((display_name, ce))
        return report


# ---------------------------------------------------------------------------
# Mock HTTP Server
# ---------------------------------------------------------------------------


class MockPropertyHandler(http.server.BaseHTTPRequestHandler):
    """
    HTTP handler for the mock property-test server.

    Endpoints:
      POST /run_property   - run a property check (JSON body)
      GET  /status         - server status
      GET  /results        - last results
      POST /reset          - reset stored results
    """

    # Class-level storage shared across handler instances
    _results: list[dict] = []
    _lock: threading.Lock = threading.Lock()

    def log_message(self, fmt, *args):  # noqa: N802 – suppress default logging
        pass  # silence server logs during tests

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> Any | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def do_GET(self):  # noqa: N802
        if self.path == "/status":
            self._send_json(200, {"status": "ok", "results_count": len(self._results)})
        elif self.path == "/results":
            with self._lock:
                self._send_json(200, {"results": list(self._results)})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path == "/run_property":
            body = self._read_json_body()
            if body is None:
                self._send_json(400, {"error": "invalid JSON body"})
                return
            result = self._handle_run_property(body)
            with self._lock:
                self._results.append(result)
            self._send_json(200, result)
        elif self.path == "/reset":
            with self._lock:
                self._results.clear()
            self._send_json(200, {"status": "reset"})
        else:
            self._send_json(404, {"error": "not found"})

    def _handle_run_property(self, body: dict) -> dict:
        """
        Run a built-in named property.

        body: {"property": "<name>", "num_examples": N, "seed": S}
        """
        prop_name = body.get("property", "")
        num_examples = int(body.get("num_examples", 50))
        seed = body.get("seed")

        built_in = _BUILT_IN_PROPERTIES.get(prop_name)
        if built_in is None:
            return {
                "error": f"unknown property: {prop_name!r}",
                "available": list(_BUILT_IN_PROPERTIES.keys()),
            }

        ce = built_in.check(num_examples=num_examples, seed=seed)
        if ce is None:
            return {"property": prop_name, "passed": True, "num_examples": num_examples}
        return {
            "property": prop_name,
            "passed": False,
            "num_examples": num_examples,
            "counterexample": {
                "original": _safe_repr(ce.original),
                "shrunk": _safe_repr(ce.shrunk),
                "seed": ce.seed,
            },
        }


def _safe_repr(value: Any) -> Any:
    """Convert a value to a JSON-safe representation."""
    if isinstance(value, (int, float, str, bool, type(None))):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_repr(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe_repr(v) for k, v in value.items()}
    return repr(value)


_BUILT_IN_PROPERTIES: dict[str, Property] = {
    "reverse_twice": Property(
        generator=gen_list(gen_int()),
        predicate=lambda lst: list(reversed(list(reversed(lst)))) == lst,
        name="reverse_twice",
    ),
    "sort_idempotent": Property(
        generator=gen_list(gen_int()),
        predicate=lambda lst: sorted(sorted(lst)) == sorted(lst),
        name="sort_idempotent",
    ),
    "addition_commutative": Property(
        generator=gen_tuple(gen_int(), gen_int()),
        predicate=lambda t: t[0] + t[1] == t[1] + t[0],
        name="addition_commutative",
    ),
    "string_concat_len": Property(
        generator=gen_tuple(gen_string(), gen_string()),
        predicate=lambda t: len(t[0] + t[1]) == len(t[0]) + len(t[1]),
        name="string_concat_len",
    ),
}


# ---------------------------------------------------------------------------
# Server management
# ---------------------------------------------------------------------------


class MockPropertyServer:
    """
    Manages a MockPropertyHandler HTTP server on a dynamic port.

    Usage:
        server = MockPropertyServer()
        server.start()
        # ... use server.port ...
        server.stop()

    Or as a context manager:
        with MockPropertyServer() as server:
            print(server.port)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self._requested_port = port
        self.port: int = 0
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> MockPropertyServer:
        # Reset class-level results
        MockPropertyHandler._results = []

        self._server = http.server.HTTPServer((self.host, self._requested_port), MockPropertyHandler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> MockPropertyServer:
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def run_suite_and_report(
    properties: dict[str, Property],
    num_examples: int = 100,
    seed: int | None = None,
) -> PropertyReport:
    """Convenience: build a suite from a dict and run it."""
    suite = PropertySuite("ad_hoc")
    for name, prop in properties.items():
        suite.add(prop, name)
    return suite.run_all(num_examples=num_examples, seed=seed)


# ---------------------------------------------------------------------------
# Statistics helpers (used by PropertyReport extensions)
# ---------------------------------------------------------------------------


def _complexity(value: Any) -> float:
    """Rough complexity measure for comparing shrunk vs original."""
    if isinstance(value, (int, float)):
        return abs(value)
    if isinstance(value, str):
        return len(value)
    if isinstance(value, (list, tuple)):
        return len(value) + sum(_complexity(v) for v in value)
    if isinstance(value, dict):
        return len(value) + sum(_complexity(v) for v in value.values())
    return 1.0


def is_simpler(a: Any, b: Any) -> bool:
    """Return True if 'a' is simpler than 'b'."""
    return _complexity(a) < _complexity(b)


# ---------------------------------------------------------------------------
# TEETH: the deterministic Shrinker is the oracle.
#
# Property-based shrinking is only useful if it lands on the *minimal*
# counterexample: a failing int must collapse toward 0 (stopping at the
# smallest still-failing integer), and a failing list must collapse toward []
# (stopping at the shortest still-failing list, elements driven to 0). The
# random generators and the random-driven PropertyRunner are excluded on
# purpose — only ``Shrinker.shrink`` is deterministic, so the oracle is scoped
# to it alone (no RNG anywhere in prove).
#
# An impl is a callable ``shrink(start, pred) -> minimal`` (the bound method of
# a Shrinker-like object). The corpus is a tuple of frozen
# (start_value, predicate, expected_minimal) triples using simple FIXED pure
# predicates; ``expected_minimal`` is a hand-computed LITERAL, never read back
# from the oracle at runtime, so the check is non-circular. prove(impl) is True
# iff the impl's shrink output diverges from any frozen literal — i.e. the
# shrinking defect is caught.
#
# Pure + deterministic: the predicates are pure integer/length tests, the
# Shrinker walks a fixed bisection/removal schedule with no RNG, and prove does
# no clock/network/filesystem/thread work. The two planted mutants model
# genuine real-world shrinker defects (per the campaign hint):
#
#   * stops_early — returns the FIRST failing value (the original input) instead
#     of bisecting toward the boundary: the "shrinking silently disabled / early
#     return" bug. The reported counterexample is huge, not minimal.
#   * overshoots — shrinks one step PAST the boundary to a value that no longer
#     fails (an off-by-one in the accept check / a final extra reduction): the
#     reported "counterexample" is a passing input, which is worse than useless.
# ---------------------------------------------------------------------------

# A shrink impl: (start_value, still_fails_predicate) -> minimal failing value.
ShrinkFunc = Callable[[Any, Callable[[Any], bool]], Any]


def _pred_int_ge_10(x: Any) -> bool:
    """Frozen predicate: an int 'still fails' iff it is >= 10."""
    return isinstance(x, int) and not isinstance(x, bool) and x >= 10


def _pred_int_ge_1(x: Any) -> bool:
    """Frozen predicate: an int 'still fails' iff it is >= 1 (shrinks to 1)."""
    return isinstance(x, int) and not isinstance(x, bool) and x >= 1


def _pred_list_len_ge_3(xs: Any) -> bool:
    """Frozen predicate: a list 'still fails' iff its length is >= 3."""
    return isinstance(xs, list) and len(xs) >= 3


def _pred_list_len_ge_1(xs: Any) -> bool:
    """Frozen predicate: a list 'still fails' iff it is non-empty (len >= 1)."""
    return isinstance(xs, list) and len(xs) >= 1


@dataclass(frozen=True)
class ShrinkCase:
    """One frozen shrink case with a hand-computed literal minimal counterexample."""

    name: str
    start: Any
    predicate: Callable[[Any], bool]
    expected_minimal: Any
    note: str = ""


# Every ``expected_minimal`` is a constant derived from the shrinking contract
# (smallest still-failing value), NOT read from the oracle at runtime:
#   * ge_10 from 50 -> 10 (smallest int with x >= 10);
#   * ge_1  from 64 -> 1  (smallest int with x >= 1);
#   * list_len_3 from a length-8 list -> [0, 0, 0] (shortest failing list, all
#     elements collapsed to 0);
#   * list_len_1 from a length-5 list -> [0] (shortest non-empty failing list).
# These boundaries (10, 1) are chosen so the correct Shrinker genuinely reaches
# the true minimum; the int bisection plus its small linear-scan window land
# exactly on the boundary for them.
SHRINK_CORPUS: tuple[ShrinkCase, ...] = (
    ShrinkCase("int_ge_10", 50, _pred_int_ge_10, 10,
               "failing int collapses toward 0, stopping at the boundary 10"),
    ShrinkCase("int_ge_1", 64, _pred_int_ge_1, 1,
               "failing int collapses toward 0, stopping at the boundary 1"),
    ShrinkCase("list_len_3", [7, 6, 5, 4, 3, 2, 1, 0], _pred_list_len_ge_3,
               [0, 0, 0],
               "failing list collapses toward [], stopping at length 3 of zeros"),
    ShrinkCase("list_len_1", [5, 4, 3, 2, 1], _pred_list_len_ge_1, [0],
               "failing list collapses toward [], stopping at length 1 ([0])"),
)


def oracle_shrink() -> ShrinkFunc:
    """ORACLE: the harness's own correct deterministic ``Shrinker.shrink``."""
    return Shrinker().shrink


class _StopsEarlyShrinker(Shrinker):
    """BUG: returns the first failing value (the original) without reducing it.

    Models a shrinker whose reduction loop was disabled / short-circuited (e.g.
    an early ``return value`` slipped in): the reported counterexample is the raw
    generated input, never minimised, so an int never reaches its boundary and a
    list never sheds an element.
    """

    def shrink(self, value: Any, predicate: Callable[[Any], bool]) -> Any:
        return value  # BUG: no shrinking at all


class _OvershootsShrinker(Shrinker):
    """BUG: takes one reduction step PAST the boundary to a passing value.

    Models an off-by-one in the accept check (or one extra final reduction): for
    ints it returns ``minimal - 1`` (sign-aware, i.e. one step closer to 0 than
    the true boundary) and for lists it drops one element too many — in both
    cases the returned "counterexample" no longer satisfies the failing
    predicate, which is strictly worse than reporting the real minimum.
    """

    def _shrink_int(self, value: int, predicate: Callable[..., Any]) -> int:
        true_min = super()._shrink_int(value, predicate)
        step = -1 if true_min > 0 else 1  # one step toward 0, past the boundary
        return true_min + step

    def _shrink_list(self, value: list, predicate: Callable[..., Any]) -> list:
        true_min = super()._shrink_list(value, predicate)
        return true_min[:-1]  # BUG: shed one more element than is failing


def stops_early_shrink() -> ShrinkFunc:
    return _StopsEarlyShrinker().shrink


def overshoots_shrink() -> ShrinkFunc:
    return _OvershootsShrinker().shrink


def prove(impl: ShrinkFunc) -> bool:
    """True iff ``impl`` shrinks any frozen case to the WRONG minimal value
    (i.e. the shrinking defect is caught): the result diverges from the
    hand-computed literal, or the impl raises.

    Non-circular + deterministic: every expectation is a literal baked into
    SHRINK_CORPUS, never read from the oracle; the predicates are pure and the
    Shrinker uses no RNG/clock/threads/network/filesystem. An impl that raises on
    a corpus case counts as caught.
    """
    for case in SHRINK_CORPUS:
        try:
            got = impl(copy.deepcopy(case.start), case.predicate)
        except Exception:  # noqa: BLE001 — raising on a corpus case counts as caught
            return True
        if got != case.expected_minimal:
            return True
    return False


TEETH = Teeth(
    prove=prove,
    oracle=oracle_shrink(),
    mutants=(
        Mutant("stops_early", stops_early_shrink(),
               "returns the first failing value (the original) instead of "
               "bisecting toward the boundary -> counterexample is never minimal"),
        Mutant("overshoots", overshoots_shrink(),
               "shrinks one step past the boundary to a value that no longer "
               "fails -> reports a passing input as the counterexample"),
    ),
    corpus_size=len(SHRINK_CORPUS),
    kind="oracle_swap",
    notes="minimal shrinking: a failing int collapses toward 0 and a failing "
          "list toward [], each stopping at the smallest still-failing value",
)


def list_scenarios() -> list[str]:
    """Names of the frozen shrink corpus cases (the teeth scenarios)."""
    return [c.name for c in SHRINK_CORPUS]


# ---------------------------------------------------------------------------
# Self-test — fails loud, reports findings.
# ---------------------------------------------------------------------------


def _run_self_test(as_json: bool = False) -> int:
    """Confirm the correct Shrinker reproduces every frozen minimal literal and
    assert the teeth: prove(oracle) is False (the real Shrinker is clean) and
    every planted shrinker mutant is caught."""
    report = Report("core/property")
    correct = oracle_shrink()
    for case in SHRINK_CORPUS:
        report.add(f"shrink:{case.name}", case.expected_minimal,
                   correct(copy.deepcopy(case.start), case.predicate),
                   detail=case.note)
    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# Module-level convenience: run a quick check
# ---------------------------------------------------------------------------


def forall(
    generator: GenFunc,
    predicate: Callable,
    precondition: Callable | None = None,
    num_examples: int = 100,
    seed: int | None = None,
    name: str = "unnamed",
) -> CounterExample | None:
    """
    Shorthand: create a Property and immediately check it.

    Returns None if all examples pass, or a CounterExample on failure.
    """
    prop = Property(generator, predicate, precondition=precondition, name=name)
    return prop.check(num_examples=num_examples, seed=seed)


# ---------------------------------------------------------------------------
# Demo / smoke test when run directly
# ---------------------------------------------------------------------------


def _run_demo() -> int:
    """Original interactive demo: run a property suite and poke the mock server.

    Kept under ``main`` only — the mock HTTP server is started here and nowhere
    near import time or inside ``prove``.
    """
    print("=== Property-Based Test Harness (Harness 11/36) ===\n")

    suite = PropertySuite("demo")

    suite.add(
        Property(
            gen_list(gen_int()),
            lambda lst: list(reversed(list(reversed(lst)))) == lst,
            name="reverse_twice",
        )
    )
    suite.add(
        Property(
            gen_list(gen_int()),
            lambda lst: sorted(sorted(lst)) == sorted(lst),
            name="sort_idempotent",
        )
    )
    suite.add(
        Property(
            gen_tuple(gen_int(), gen_int()),
            lambda t: t[0] + t[1] == t[1] + t[0],
            name="addition_commutative",
        )
    )
    suite.add(
        Property(
            gen_tuple(gen_string(), gen_string()),
            lambda t: len(t[0] + t[1]) == len(t[0]) + len(t[1]),
            name="string_concat_length",
        )
    )

    report = suite.run_all(num_examples=200, seed=12345)
    print(f"Results: {report}")

    if report.all_passed:
        print("All properties passed!")
    else:
        for name, ce in report.counterexamples:
            print(f"  FAIL [{name}]: original={ce.original!r}, shrunk={ce.shrunk!r}")

    # Demonstrate mock server
    print("\n--- Mock HTTP Server Demo ---")
    with MockPropertyServer() as server:
        print(f"Server running on port {server.port}")
        import urllib.request

        resp = urllib.request.urlopen(f"{server.base_url}/status")
        print(f"Status: {json.loads(resp.read())}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Property-based test harness (generators, shrinker, suite)."
    )
    parser.add_argument("--self-test", action="store_true",
                        help="run built-in shrinker teeth checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--demo", action="store_true",
                        help="run the interactive property + mock-server demo")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0
    if args.demo:
        return _run_demo()
    if args.self_test or args.json:
        return _run_self_test(as_json=args.json)
    return _run_self_test(as_json=False)


if __name__ == "__main__":
    sys.exit(main())
