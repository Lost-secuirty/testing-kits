"""
Data Pipeline / ETL Test Harness (Harness 19 of 36)
Pure stdlib, zero external dependencies.
Mock HTTP server on dynamic port (default 19050).

Teeth (campaign 2026): the oracle-able core is the group ``Aggregator`` — a pure
group-by transform that computes per-group SUM / COUNT / AVG. A frozen corpus of
input records carries its hand-computed per-group aggregates as literal
expectations, chosen so AVG (sum / value-count) cannot be confused with a
divide-by-group-count, and so a sum that drops the first record diverges on every
non-trivial group. ``prove`` judges an aggregator impl against those frozen
literals (never against the oracle at runtime), so the check is non-circular and
pure: no clock, network, filesystem, threads, or RNG. The mock HTTP server stays
under ``main`` only and is never touched by the teeth path.

Self-test:
  python harnesses/core/pipeline_test_harness.py --self-test
  python harnesses/core/pipeline_test_harness.py --json
  python harnesses/core/pipeline_test_harness.py --list-scenarios
"""

from __future__ import annotations

import argparse
import enum
import json
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer

# Make the shared teeth contract importable whether run as a module or a script.
from pathlib import Path as _Path
from typing import Any

if str(_Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(_Path(__file__).resolve().parents[2]))
from harnesses._teeth import Mutant, Report, Teeth  # noqa: E402

# ---------------------------------------------------------------------------
# Base Classes
# ---------------------------------------------------------------------------

class PipelineStage:
    """Base class for all pipeline stages."""

    def process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process a list of records and return transformed records."""
        return records

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ---------------------------------------------------------------------------
# Schema Validation
# ---------------------------------------------------------------------------

class SchemaSpec:
    """Field name → type mapping for schema validation."""

    SUPPORTED_TYPES = {
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
        "list": list,
        "dict": dict,
        "any": None,  # No type check
    }

    def __init__(self, fields: dict[str, Any]):
        """
        fields: dict mapping field_name -> type or type string
        e.g. {"id": int, "name": str} or {"id": "int", "name": "str"}
        Can also specify required: {"id": {"type": int, "required": True}}
        """
        self.fields: dict[str, dict[str, Any]] = {}
        for name, spec in fields.items():
            if isinstance(spec, dict):
                self.fields[name] = spec
            elif isinstance(spec, str):
                resolved = self.SUPPORTED_TYPES.get(spec, str)
                self.fields[name] = {"type": resolved, "required": True}
            else:
                # Assume it's a type directly
                self.fields[name] = {"type": spec, "required": True}

    def get_field_type(self, field_name: str) -> type | None:
        spec = self.fields.get(field_name)
        if spec is None:
            return None
        return spec.get("type")

    def is_required(self, field_name: str) -> bool:
        spec = self.fields.get(field_name)
        if spec is None:
            return False
        return spec.get("required", True)

    def field_names(self) -> list[str]:
        return list(self.fields.keys())


class SchemaValidator(PipelineStage):
    """Validates each record against a SchemaSpec; collects errors."""

    def __init__(self, schema: SchemaSpec, strict: bool = False):
        """
        schema: SchemaSpec to validate against
        strict: if True, records with validation errors are dropped
        """
        self.schema = schema
        self.strict = strict
        self.errors: list[dict[str, Any]] = []

    def validate_record(self, record: dict[str, Any], index: int) -> list[str]:
        """Validate a single record. Returns list of error messages."""
        errs = []
        for field_name, spec in self.schema.fields.items():
            required = spec.get("required", True)
            expected_type = spec.get("type")

            if field_name not in record:
                if required:
                    errs.append(f"Record {index}: missing required field '{field_name}'")
                continue

            value = record[field_name]
            if value is None:
                continue  # Null handling done by NullHandler

            if expected_type is not None and not isinstance(value, expected_type):
                errs.append(
                    f"Record {index}: field '{field_name}' expected {expected_type.__name__}, "
                    f"got {type(value).__name__}"
                )
        return errs

    def process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.errors = []
        valid_records = []
        for i, record in enumerate(records):
            errs = self.validate_record(record, i)
            if errs:
                for err in errs:
                    self.errors.append({"index": i, "record": record, "error": err})
                if not self.strict:
                    valid_records.append(record)
                # In strict mode, drop the record
            else:
                valid_records.append(record)
        return valid_records

    def get_errors(self) -> list[dict[str, Any]]:
        return self.errors

    def has_errors(self) -> bool:
        return len(self.errors) > 0


# ---------------------------------------------------------------------------
# Null Handling
# ---------------------------------------------------------------------------

class NullStrategy(enum.Enum):
    DROP = "drop"
    DEFAULT = "default"
    PROPAGATE = "propagate"


class NullHandler(PipelineStage):
    """Handles null/None fields according to a strategy."""

    def __init__(
        self,
        strategy: NullStrategy = NullStrategy.PROPAGATE,
        fields: list[str] | None = None,
        defaults: dict[str, Any] | None = None,
    ):
        """
        strategy: NullStrategy enum (DROP/DEFAULT/PROPAGATE)
        fields: which fields to check for nulls (None = all fields)
        defaults: default values per field (used with DEFAULT strategy)
        """
        self.strategy = strategy
        self.fields = fields
        self.defaults = defaults or {}
        self.dropped_count = 0

    def _has_null(self, record: dict[str, Any], fields_to_check: list[str]) -> bool:
        return any(record.get(f) is None for f in fields_to_check)

    def process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.dropped_count = 0
        result = []
        for record in records:
            fields_to_check = self.fields if self.fields is not None else list(record.keys())

            if self.strategy == NullStrategy.PROPAGATE:
                result.append(record)
            elif self.strategy == NullStrategy.DROP:
                if self._has_null(record, fields_to_check):
                    self.dropped_count += 1
                else:
                    result.append(record)
            elif self.strategy == NullStrategy.DEFAULT:
                new_record = dict(record)
                for f in fields_to_check:
                    if new_record.get(f) is None and f in self.defaults:
                        new_record[f] = self.defaults[f]
                result.append(new_record)
        return result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class Deduplicator(PipelineStage):
    """Removes duplicate records by key field(s)."""

    def __init__(self, key_fields: str | list[str], keep: str = "first"):
        """
        key_fields: field name or list of field names forming the unique key
        keep: 'first' or 'last' - which duplicate to keep
        """
        if isinstance(key_fields, str):
            self.key_fields = [key_fields]
        else:
            self.key_fields = key_fields
        self.keep = keep
        self.duplicate_count = 0

    def _make_key(self, record: dict[str, Any]) -> tuple:
        return tuple(record.get(f) for f in self.key_fields)

    def process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.duplicate_count = 0
        seen = {}

        if self.keep == "first":
            for record in records:
                key = self._make_key(record)
                if key not in seen:
                    seen[key] = record
                else:
                    self.duplicate_count += 1
        else:  # last
            for record in records:
                key = self._make_key(record)
                if key in seen:
                    self.duplicate_count += 1
                seen[key] = record

        return list(seen.values())


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class ReconciliationResult:
    """Result of a reconciliation check."""

    def __init__(
        self,
        source_count: int,
        dest_count: int,
        match: bool,
        discrepancy: int,
        field_mismatches: list[dict[str, Any]] | None = None,
    ):
        self.source_count = source_count
        self.dest_count = dest_count
        self.match = match
        self.discrepancy = discrepancy
        self.field_mismatches = field_mismatches or []

    def __repr__(self) -> str:
        return (
            f"ReconciliationResult(source={self.source_count}, dest={self.dest_count}, "
            f"match={self.match}, discrepancy={self.discrepancy})"
        )


class Reconciler:
    """Checks row counts and optionally field values between source and destination."""

    def __init__(self, tolerance: int = 0, check_fields: list[str] | None = None):
        """
        tolerance: acceptable count discrepancy (0 = exact match required)
        check_fields: fields to check for value mismatches (None = no field check)
        """
        self.tolerance = tolerance
        self.check_fields = check_fields

    def reconcile(
        self,
        source: list[dict[str, Any]],
        destination: list[dict[str, Any]],
        key_field: str | None = None,
    ) -> ReconciliationResult:
        """Compare source and destination datasets."""
        src_count = len(source)
        dst_count = len(destination)
        discrepancy = abs(src_count - dst_count)
        match = discrepancy <= self.tolerance

        field_mismatches = []
        if self.check_fields and key_field:
            src_index = {r[key_field]: r for r in source if key_field in r}
            dst_index = {r[key_field]: r for r in destination if key_field in r}
            for key, src_rec in src_index.items():
                if key in dst_index:
                    dst_rec = dst_index[key]
                    for field in self.check_fields:
                        sv = src_rec.get(field)
                        dv = dst_rec.get(field)
                        if sv != dv:
                            field_mismatches.append({
                                "key": key,
                                "field": field,
                                "source_value": sv,
                                "dest_value": dv,
                            })

        return ReconciliationResult(
            source_count=src_count,
            dest_count=dst_count,
            match=match,
            discrepancy=discrepancy,
            field_mismatches=field_mismatches,
        )


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class AggregateFunction(enum.Enum):
    SUM = "sum"
    COUNT = "count"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class Aggregator(PipelineStage):
    """Computes aggregations per group."""

    def __init__(
        self,
        group_by: str | list[str],
        aggregations: dict[str, AggregateFunction],
    ):
        """
        group_by: field(s) to group by
        aggregations: mapping of output_field -> AggregateFunction
                      e.g. {"total_sales": AggregateFunction.SUM} applied to a field
                      For more detail: {"total_sales": (AggregateFunction.SUM, "sales_field")}
        """
        if isinstance(group_by, str):
            self.group_by = [group_by]
        else:
            self.group_by = group_by

        # Normalize aggregations: can be AggregateFunction or (AggregateFunction, field_name)
        self.aggregations: dict[str, tuple[AggregateFunction, str]] = {}
        for out_field, agg_spec in aggregations.items():
            if isinstance(agg_spec, tuple):
                func, src_field = agg_spec
                self.aggregations[out_field] = (func, src_field)
            else:
                # Use out_field as source field too
                self.aggregations[out_field] = (agg_spec, out_field)

    def _make_group_key(self, record: dict[str, Any]) -> tuple:
        return tuple(record.get(f) for f in self.group_by)

    def process(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            key = self._make_group_key(record)
            groups[key].append(record)

        result = []
        for group_key, group_records in groups.items():
            agg_record = {}
            # Add group-by fields
            for i, field in enumerate(self.group_by):
                agg_record[field] = group_key[i]

            # Compute aggregations
            for out_field, (func, src_field) in self.aggregations.items():
                values = [r.get(src_field) for r in group_records if r.get(src_field) is not None]

                if func == AggregateFunction.COUNT:
                    agg_record[out_field] = len(group_records)
                elif func == AggregateFunction.SUM:
                    agg_record[out_field] = sum(values) if values else 0
                elif func == AggregateFunction.AVG:
                    agg_record[out_field] = sum(values) / len(values) if values else 0
                elif func == AggregateFunction.MIN:
                    agg_record[out_field] = min(values) if values else None
                elif func == AggregateFunction.MAX:
                    agg_record[out_field] = max(values) if values else None

            result.append(agg_record)

        return result


# ---------------------------------------------------------------------------
# Joining
# ---------------------------------------------------------------------------

class JoinType(enum.Enum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"


class Joiner:
    """Joins two datasets on a key field."""

    def __init__(self, join_type: JoinType = JoinType.INNER, key_field: str = "id"):
        self.join_type = join_type
        self.key_field = key_field

    def join(
        self,
        left: list[dict[str, Any]],
        right: list[dict[str, Any]],
        left_prefix: str = "left_",
        right_prefix: str = "right_",
    ) -> list[dict[str, Any]]:
        """Perform the join. Returns merged records."""
        # Index right by key
        right_index: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in right:
            key = r.get(self.key_field)
            right_index[key].append(r)

        left_index: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for r in left:
            key = r.get(self.key_field)
            left_index[key].append(r)

        result = []

        if self.join_type in (JoinType.INNER, JoinType.LEFT):
            for l_rec in left:
                key = l_rec.get(self.key_field)
                matching_right = right_index.get(key, [])

                if matching_right:
                    for r_rec in matching_right:
                        merged = self._merge_records(l_rec, r_rec, left_prefix, right_prefix)
                        result.append(merged)
                elif self.join_type == JoinType.LEFT:
                    merged = self._merge_records(l_rec, {}, left_prefix, right_prefix)
                    result.append(merged)

        if self.join_type == JoinType.RIGHT:
            for r_rec in right:
                key = r_rec.get(self.key_field)
                matching_left = left_index.get(key, [])

                if matching_left:
                    for l_rec in matching_left:
                        merged = self._merge_records(l_rec, r_rec, left_prefix, right_prefix)
                        result.append(merged)
                else:
                    merged = self._merge_records({}, r_rec, left_prefix, right_prefix)
                    result.append(merged)

        return result

    def _merge_records(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        left_prefix: str,
        right_prefix: str,
    ) -> dict[str, Any]:
        """Merge two records, prefixing conflicting fields."""
        merged = {}
        # Add key field once
        key = left.get(self.key_field) if left else right.get(self.key_field)
        merged[self.key_field] = key

        # Add left fields
        for k, v in left.items():
            if k == self.key_field:
                continue
            if k in right and k != self.key_field:
                merged[f"{left_prefix}{k}"] = v
            else:
                merged[k] = v

        # Add right fields
        for k, v in right.items():
            if k == self.key_field:
                continue
            if k in left and k != self.key_field:
                merged[f"{right_prefix}{k}"] = v
            else:
                merged[k] = v

        return merged


# ---------------------------------------------------------------------------
# Pipeline Runner
# ---------------------------------------------------------------------------

class PipelineReport:
    """Summary report for a pipeline run."""

    def __init__(
        self,
        rows_in: int = 0,
        rows_out: int = 0,
        rows_dropped: int = 0,
        errors: list[Any] | None = None,
        throughput: float = 0.0,
        elapsed_seconds: float = 0.0,
        stage_reports: list[dict[str, Any]] | None = None,
    ):
        self.rows_in = rows_in
        self.rows_out = rows_out
        self.rows_dropped = rows_dropped
        self.errors = errors or []
        self.throughput = throughput  # rows/sec
        self.elapsed_seconds = elapsed_seconds
        self.stage_reports = stage_reports or []

    def __repr__(self) -> str:
        return (
            f"PipelineReport(rows_in={self.rows_in}, rows_out={self.rows_out}, "
            f"rows_dropped={self.rows_dropped}, errors={len(self.errors)}, "
            f"throughput={self.throughput:.1f} rows/sec)"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "rows_dropped": self.rows_dropped,
            "error_count": len(self.errors),
            "errors": self.errors,
            "throughput": self.throughput,
            "elapsed_seconds": self.elapsed_seconds,
            "stage_reports": self.stage_reports,
        }


class PipelineRunner:
    """Chains pipeline stages and runs them, measuring throughput."""

    def __init__(self, stages: list[PipelineStage] | None = None):
        self.stages: list[PipelineStage] = stages or []
        self._last_report: PipelineReport | None = None

    def add_stage(self, stage: PipelineStage) -> PipelineRunner:
        self.stages.append(stage)
        return self

    def run(self, records: list[dict[str, Any]]) -> PipelineReport:
        """Run all stages and return a PipelineReport."""
        rows_in = len(records)
        all_errors = []
        stage_reports = []

        start_time = time.perf_counter()
        current_records = list(records)

        for stage in self.stages:
            stage_in = len(current_records)
            current_records = stage.process(current_records)
            stage_out = len(current_records)

            # Collect errors from validators
            if isinstance(stage, SchemaValidator):
                all_errors.extend(stage.errors)

            stage_reports.append({
                "stage": repr(stage),
                "rows_in": stage_in,
                "rows_out": stage_out,
                "rows_dropped": max(0, stage_in - stage_out),
            })

        end_time = time.perf_counter()
        elapsed = end_time - start_time
        rows_out = len(current_records)
        rows_dropped = rows_in - rows_out
        throughput = rows_in / elapsed if elapsed > 0 else float("inf")

        self._last_report = PipelineReport(
            rows_in=rows_in,
            rows_out=rows_out,
            rows_dropped=max(0, rows_dropped),
            errors=all_errors,
            throughput=throughput,
            elapsed_seconds=elapsed,
            stage_reports=stage_reports,
        )
        return self._last_report

    def get_last_report(self) -> PipelineReport | None:
        return self._last_report

    def clear_stages(self) -> None:
        self.stages = []


# ---------------------------------------------------------------------------
# Mock HTTP Server
# ---------------------------------------------------------------------------

class MockPipelineHandler(BaseHTTPRequestHandler):
    """HTTP handler for the mock pipeline server."""

    # Class-level pipeline state
    pipeline_data: list[dict[str, Any]] = []
    pipeline_report: dict[str, Any] | None = None

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default access logging."""
        pass

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> Any | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
        elif self.path == "/records":
            self._send_json({"records": MockPipelineHandler.pipeline_data})
        elif self.path == "/report":
            if MockPipelineHandler.pipeline_report:
                self._send_json(MockPipelineHandler.pipeline_report)
            else:
                self._send_json({"error": "no report available"}, 404)
        elif self.path == "/stats":
            self._send_json({
                "record_count": len(MockPipelineHandler.pipeline_data),
                "has_report": MockPipelineHandler.pipeline_report is not None,
            })
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        body = self._read_body()

        if self.path == "/records":
            if body and isinstance(body, list):
                MockPipelineHandler.pipeline_data = body
                self._send_json({"status": "ok", "count": len(body)})
            elif body and isinstance(body, dict) and "records" in body:
                MockPipelineHandler.pipeline_data = body["records"]
                self._send_json({"status": "ok", "count": len(body["records"])})
            else:
                self._send_json({"error": "invalid payload"}, 400)

        elif self.path == "/report":
            if body and isinstance(body, dict):
                MockPipelineHandler.pipeline_report = body
                self._send_json({"status": "ok"})
            else:
                self._send_json({"error": "invalid payload"}, 400)

        elif self.path == "/run":
            # Run a simple pipeline on submitted records
            if body and isinstance(body, dict):
                records = body.get("records", [])
                MockPipelineHandler.pipeline_data = records
                runner = PipelineRunner()
                report = runner.run(records)
                MockPipelineHandler.pipeline_report = report.to_dict()
                self._send_json(report.to_dict())
            else:
                self._send_json({"error": "invalid payload"}, 400)

        elif self.path == "/clear":
            MockPipelineHandler.pipeline_data = []
            MockPipelineHandler.pipeline_report = None
            self._send_json({"status": "cleared"})

        else:
            self._send_json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        if self.path == "/records":
            MockPipelineHandler.pipeline_data = []
            self._send_json({"status": "cleared"})
        else:
            self._send_json({"error": "not found"}, 404)


class MockPipelineServer:
    """Wrapper for the mock pipeline HTTP server."""

    def __init__(self, port: int = 0):
        """port=0 means OS assigns a dynamic port."""
        self.port = port
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> int:
        """Start server. Returns the actual port."""
        # Reset state
        MockPipelineHandler.pipeline_data = []
        MockPipelineHandler.pipeline_report = None

        self._server = HTTPServer(("127.0.0.1", self.port), MockPipelineHandler)
        self.port = self._server.server_address[1]

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.port

    def stop(self) -> None:
        server = self._server
        if server:
            server.shutdown()
            server.server_close()
            self._server = None
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def get_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self) -> MockPipelineServer:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()


# Expose default port constant
DEFAULT_PORT = 19050


# ---------------------------------------------------------------------------
# TEETH: a FROZEN corpus of input records -> the EXACT per-group aggregate a
# correct group-by transform MUST produce.
#
# The oracle-able core is the ``Aggregator`` group transform: bucket records by
# ``dept`` and compute, per group, COUNT, SUM(salary) and AVG(salary). A
# group-by aggregator only has teeth if it CATCHES the two defects a real dev
# ships when hand-rolling the fold:
#
#   * AVG divides each group's SUM by the NUMBER OF GROUPS (a stray
#     ``len(groups)`` where ``len(group_records)`` was meant) instead of by the
#     count of values IN that group — correct only by coincidence when a group's
#     value-count happens to equal the group-count;
#   * SUM folds from the SECOND record (``records[1:]`` / an accumulator seeded
#     with the first element and the loop also skipping it), silently dropping
#     the first value of every group.
#
# An impl is a callable ``aggregate(records) -> tuple[GroupAgg, ...]`` returning
# one normalized, key-sorted aggregate per group. ``prove`` judges each impl
# against the corpus's FROZEN LITERAL aggregates (hand-computed below, NEVER read
# back from the oracle at runtime), so the check is non-circular. ``prove(impl)``
# is True iff any group's (count, total, avg) diverges from its frozen literal —
# i.e. the planted aggregation bug is caught.
#
# Pure + deterministic: integer / float arithmetic over a frozen tuple only — no
# clock (the ``perf_counter`` throughput path is excluded), no RNG, no threads,
# no network, no filesystem. Group iteration order is normalized away by sorting
# on the group key, so dict ordering cannot make the check flaky.
# ---------------------------------------------------------------------------

AGG_GROUP_BY = "dept"
AGG_SRC_FIELD = "salary"


@dataclass(frozen=True)
class GroupAgg:
    """One group's expected aggregate — every number is a frozen literal."""

    key: str        # the group-by value (dept)
    count: int      # number of records in the group
    total: int      # SUM of salary over the group
    avg: float      # AVG of salary over the group == total / count


# Frozen input records. Groups are deliberately UNEQUAL in size (3 / 2 / 1) and
# there are exactly THREE groups, so dividing a group's SUM by the group-count
# (3) disagrees with the true AVG on every group whose value-count != 3.
AGG_RECORDS: tuple[dict[str, Any], ...] = (
    {"dept": "eng", "salary": 100},
    {"dept": "eng", "salary": 200},
    {"dept": "eng", "salary": 300},
    {"dept": "hr", "salary": 150},
    {"dept": "hr", "salary": 50},
    {"dept": "ops", "salary": 90},
)


# Hand-computed expectations (constants, never derived at runtime):
#   eng: [100,200,300] -> count 3, total 600, avg 200.0
#   hr : [150,50]      -> count 2, total 200, avg 100.0
#   ops: [90]          -> count 1, total  90, avg  90.0
# Discriminating cases:
#   * avg_div_by_groupcount divides by 3 (the group-count): eng 600/3=200.0
#     coincides, but hr 200/3≈66.67 != 100.0 and ops 90/3=30.0 != 90.0 -> caught.
#   * sum_skips_first drops the first value: eng 500 != 600, hr 50 != 200,
#     ops 0 != 90 -> caught on every group.
AGG_CORPUS: tuple[GroupAgg, ...] = (
    GroupAgg("eng", 3, 600, 200.0),
    GroupAgg("hr", 2, 200, 100.0),
    GroupAgg("ops", 1, 90, 90.0),
)


def _normalize(rows: list[dict[str, Any]]) -> tuple[GroupAgg, ...]:
    """Project aggregator output rows into key-sorted ``GroupAgg`` tuples so
    comparison is independent of group iteration order."""
    out = []
    for r in rows:
        out.append(GroupAgg(
            key=r[AGG_GROUP_BY],
            count=int(r["count"]),
            total=int(r["total"]),
            avg=float(r["avg"]),
        ))
    return tuple(sorted(out, key=lambda g: g.key))


# --- ORACLE: reuse the harness's own correct ``Aggregator`` group transform ---

def oracle_aggregate(records: list[dict[str, Any]]) -> tuple[GroupAgg, ...]:
    """Correct per-group COUNT / SUM / AVG, delegating to the harness's own
    ``Aggregator``. Returns one key-sorted ``GroupAgg`` per group."""
    agg = Aggregator(
        group_by=AGG_GROUP_BY,
        aggregations={
            "count": (AggregateFunction.COUNT, AGG_SRC_FIELD),
            "total": (AggregateFunction.SUM, AGG_SRC_FIELD),
            "avg": (AggregateFunction.AVG, AGG_SRC_FIELD),
        },
    )
    return _normalize(agg.process(records))


# --- Planted buggy twins (each models a real group-by fold defect) -----------

def avg_div_by_groupcount(records: list[dict[str, Any]]) -> tuple[GroupAgg, ...]:
    """BUG: computes AVG as ``group_sum / number_of_groups`` instead of
    ``group_sum / number_of_values_in_the_group``.

    Models the stray-``len(groups)`` defect: the developer divides by the count
    of buckets (held in scope) rather than the size of the current bucket. COUNT
    and SUM are correct; only AVG is wrong, and only on groups whose value-count
    differs from the group-count.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        groups[rec[AGG_GROUP_BY]].append(rec)
    n_groups = len(groups)  # BUG: wrong denominator for AVG
    rows = []
    for key, members in groups.items():
        values = [m[AGG_SRC_FIELD] for m in members if m.get(AGG_SRC_FIELD) is not None]
        total = sum(values)
        rows.append({
            AGG_GROUP_BY: key,
            "count": len(members),
            "total": total,
            "avg": (total / n_groups) if n_groups else 0.0,
        })
    return _normalize(rows)


def sum_skips_first(records: list[dict[str, Any]]) -> tuple[GroupAgg, ...]:
    """BUG: folds each group's SUM (and therefore AVG) starting from the SECOND
    record, dropping the first value of every group.

    Models the classic ``for v in values[1:]`` / accumulator-seeded-then-skipped
    off-by-one in a hand-rolled reducer. COUNT stays correct (it counts members),
    so the row count looks right while the totals are silently low.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in records:
        groups[rec[AGG_GROUP_BY]].append(rec)
    rows = []
    for key, members in groups.items():
        values = [m[AGG_SRC_FIELD] for m in members if m.get(AGG_SRC_FIELD) is not None]
        total = sum(values[1:])  # BUG: skips the first value of the group
        count = len(members)
        rows.append({
            AGG_GROUP_BY: key,
            "count": count,
            "total": total,
            "avg": (total / count) if count else 0.0,
        })
    return _normalize(rows)


def prove(impl: Callable[[list[dict[str, Any]]], tuple[GroupAgg, ...]]) -> bool:
    """True iff ``impl`` produces an aggregate that diverges from any frozen
    corpus literal (i.e. the aggregation bug is caught): a group is missing /
    extra, or its (count, total, avg) differs from the hand-computed literal, or
    the impl raises.

    Non-circular + deterministic: every expectation lives in ``AGG_CORPUS`` as a
    literal, never read from the oracle; arithmetic over a frozen tuple only, no
    RNG/clock/threads/network/filesystem. An impl that raises counts as caught.
    """
    try:
        produced = tuple(sorted(impl([dict(r) for r in AGG_RECORDS]),
                                key=lambda g: g.key))
    except Exception:  # noqa: BLE001 — raising on the corpus counts as caught
        return True
    return produced != AGG_CORPUS


# Vacuity gate: neutering the oracle must turn this harness's self-test red.
VACUITY_TARGETS = ["oracle_aggregate"]

TEETH = Teeth(
    prove=prove,
    oracle=oracle_aggregate,
    mutants=(
        Mutant("avg_div_by_groupcount", avg_div_by_groupcount,
               "AVG divides each group's sum by the NUMBER OF GROUPS instead of "
               "the count of values in the group -> wrong on any group whose "
               "value-count != group-count"),
        Mutant("sum_skips_first", sum_skips_first,
               "SUM folds from the second record (values[1:]) -> drops the first "
               "value of every group, so totals and AVG run low"),
    ),
    corpus_size=len(AGG_CORPUS),
    kind="oracle_swap",
    notes="group-by aggregator: per group, AVG == SUM(values) / COUNT(values), "
          "not SUM / number_of_groups, and SUM must include the first value",
)


def list_scenarios() -> list[str]:
    """Names of the frozen aggregation corpus groups (the teeth scenarios)."""
    return [g.key for g in AGG_CORPUS]


# ---------------------------------------------------------------------------
# Self-test — fails loud, reports findings.
# ---------------------------------------------------------------------------

def _run_self_test(as_json: bool = False) -> int:
    """Assert the group aggregator reproduces every frozen per-group literal and
    that the teeth hold: prove(oracle) is False and every planted mutant is
    caught."""
    report = Report("core/pipeline")

    produced = {g.key: g for g in oracle_aggregate([dict(r) for r in AGG_RECORDS])}
    for expected in AGG_CORPUS:
        got = produced.get(expected.key)
        report.add(
            f"agg:{expected.key}",
            [expected.count, expected.total, expected.avg],
            None if got is None else [got.count, got.total, got.avg],
            detail=f"per-group COUNT/SUM/AVG for dept={expected.key!r}",
        )

    report.assert_teeth(TEETH)
    return report.emit(as_json=as_json)


# ---------------------------------------------------------------------------
# CLI — default action is the self-test (repo convention).
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Data pipeline / ETL controls — group-aggregator teeth"
    )
    parser.add_argument("--self-test", action="store_true", help="run built-in checks")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable findings (implies --self-test)")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--serve", action="store_true",
                        help="run the mock pipeline HTTP server (blocks)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="port for --serve (default 19050; 0 = OS-assigned)")
    args = parser.parse_args(argv)

    if args.list_scenarios:
        print("\n".join(list_scenarios()))
        return 0

    if args.serve:
        # serve_forever stays under main only — never at import, never in prove.
        server = MockPipelineServer(port=args.port)
        actual = server.start()
        print(f"serving mock pipeline on http://127.0.0.1:{actual}", file=sys.stderr)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            server.stop()
        return 0

    return _run_self_test(as_json=args.json)


if __name__ == "__main__":
    sys.exit(main())
