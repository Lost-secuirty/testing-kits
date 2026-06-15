#!/usr/bin/env python3
"""
cardinality_test_harness.py — Metric / cache / log cardinality explosion detector.
==================================================================================

Pure-stdlib. Zero external dependencies.

Per-request-ID metric labels tank Prometheus/Mimir; URL-with-query-string
cache keys blow the LRU; per-user DB-index columns explode storage
(Sawmills 2025).

This harness watches a key-emitting function over a workload and:
  - Counts distinct values per dimension.
  - Computes the *growth rate*: how cardinality scales with workload size.
  - Flags any dimension whose cardinality grows linearly (or super-linearly)
    in the number of requests — the signature of an unbounded sink.

Usage:
  python harnesses/core/cardinality_test_harness.py --self-test
  python harnesses/core/cardinality_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class CardinalityReport:
    dimension: str
    samples: int
    distinct: int
    growth_ratio: float  # distinct / samples in [0, 1]
    bounded: bool
    detail: str = ""


@dataclass
class CardinalityConfig:
    samples: int = 1000
    growth_threshold: float = 0.5  # >50% distinct/samples = unbounded


class CardinalityProbe:
    """Track distinct values per dimension over a stream of (dim, value) emits."""

    def __init__(self, dimensions: list[str]):
        self.dimensions = dimensions
        self._counts: dict[str, set[str]] = {d: set() for d in dimensions}
        self._samples: dict[str, int] = {d: 0 for d in dimensions}

    def emit(self, dimension: str, value: Any) -> None:
        if dimension not in self._counts:
            self._counts[dimension] = set()
            self._samples[dimension] = 0
            self.dimensions.append(dimension)
        self._counts[dimension].add(str(value))
        self._samples[dimension] += 1

    def report(self, threshold: float = 0.5) -> list[CardinalityReport]:
        out: list[CardinalityReport] = []
        for dim in self.dimensions:
            n = self._samples[dim]
            d = len(self._counts[dim])
            ratio = d / n if n else 0.0
            bounded = ratio <= threshold
            out.append(CardinalityReport(
                dimension=dim, samples=n, distinct=d, growth_ratio=ratio,
                bounded=bounded,
                detail=("growing linearly" if not bounded else "stable"),
            ))
        return out


def assert_bounded_cardinality(probe: CardinalityProbe, dim: str,
                               max_distinct: int) -> None:
    distinct = len(probe._counts.get(dim, set()))
    if distinct > max_distinct:
        raise AssertionError(
            f"dimension {dim!r} has {distinct} distinct values, "
            f"exceeds bound {max_distinct}"
        )


# ---------------------------------------------------------------------------
# Self-test fixtures
# ---------------------------------------------------------------------------


def emit_metric_labels_bounded(probe: CardinalityProbe, samples: int) -> None:
    """User-tier label: only 3 values, bounded."""
    tiers = ["free", "pro", "enterprise"]
    for i in range(samples):
        probe.emit("user_tier", tiers[i % 3])


def emit_metric_labels_unbounded(probe: CardinalityProbe, samples: int) -> None:
    """Request-ID label: unique per request, unbounded — the classic bug."""
    for _i in range(samples):
        probe.emit("request_id", str(uuid.uuid4()))


def emit_cache_keys_unbounded(probe: CardinalityProbe, samples: int) -> None:
    """URL with query string as cache key: blows the LRU."""
    for i in range(samples):
        url = f"/api/users?session={uuid.uuid4().hex}&ts={i}"
        probe.emit("cache_key", url)


def emit_cache_keys_bounded(probe: CardinalityProbe, samples: int) -> None:
    """Route-template cache key: bounded by the number of distinct routes."""
    routes = ["/api/users", "/api/orders", "/api/items"]
    for i in range(samples):
        probe.emit("cache_key_template", routes[i % len(routes)])


def emit_db_index_unbounded(probe: CardinalityProbe, samples: int) -> None:
    """Index on a free-text field: unique per row."""
    for i in range(samples):
        probe.emit("comment_text", f"user comment {i} {uuid.uuid4()}")


def emit_db_index_bounded(probe: CardinalityProbe, samples: int) -> None:
    """Index on status field: bounded by enum size."""
    statuses = ["pending", "active", "cancelled", "completed"]
    for i in range(samples):
        probe.emit("order_status", statuses[i % len(statuses)])


SCENARIOS: dict[str, tuple[Callable, str, bool]] = {
    "metric_labels_user_tier": (emit_metric_labels_bounded, "user_tier", True),
    "metric_labels_request_id": (emit_metric_labels_unbounded, "request_id", False),
    "cache_keys_url_with_query": (emit_cache_keys_unbounded, "cache_key", False),
    "cache_keys_route_template": (emit_cache_keys_bounded, "cache_key_template", True),
    "db_index_comment_text": (emit_db_index_unbounded, "comment_text", False),
    "db_index_order_status": (emit_db_index_bounded, "order_status", True),
}


def list_scenarios() -> list[str]:
    return list(SCENARIOS.keys())


def _run_self_test(config: CardinalityConfig, verbose: bool = False) -> int:
    failures: list[str] = []
    for name, (emit_fn, dim, expected_bounded) in SCENARIOS.items():
        probe = CardinalityProbe([dim])
        emit_fn(probe, config.samples)
        report = probe.report(threshold=config.growth_threshold)
        bounded = report[0].bounded
        if bounded != expected_bounded:
            failures.append(
                f"{name}: expected bounded={expected_bounded} got bounded={bounded} "
                f"(distinct={report[0].distinct}/{report[0].samples})"
            )
        mark = "OK  " if bounded == expected_bounded else "FAIL"
        print(f"  {mark}  {name:32s} distinct={report[0].distinct:5d}/"
              f"{report[0].samples:5d}  ratio={report[0].growth_ratio:.3f}  "
              f"bounded={bounded}")
    if failures:
        print("FAILED:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print(f"OK: {len(SCENARIOS)} scenarios matched their cardinality expectations.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cardinality-explosion detector")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--list-scenarios", action="store_true")
    p.add_argument("--samples", type=int, default=1000)
    p.add_argument("--threshold", type=float, default=0.5)
    p.add_argument("--verbose", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if args.list_scenarios:
        for s in list_scenarios():
            print(s)
        return 0
    config = CardinalityConfig(samples=args.samples, growth_threshold=args.threshold)
    if args.self_test:
        return _run_self_test(config, verbose=args.verbose)
    build_parser().print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
