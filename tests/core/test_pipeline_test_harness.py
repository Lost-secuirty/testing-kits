"""
Tests for pipeline_test_harness.py
~78 tests covering all pipeline stages.
"""

import json
import unittest
import urllib.error
import urllib.request

import harnesses.core.pipeline_test_harness as pipeline_mod
from harnesses.core.pipeline_test_harness import (
    AGG_CORPUS,
    AGG_RECORDS,
    DEFAULT_PORT,
    TEETH,
    AggregateFunction,
    Aggregator,
    Deduplicator,
    Joiner,
    JoinType,
    MockPipelineServer,
    NullHandler,
    NullStrategy,
    PipelineReport,
    PipelineRunner,
    PipelineStage,
    Reconciler,
    ReconciliationResult,
    SchemaSpec,
    SchemaValidator,
    avg_div_by_groupcount,
    oracle_aggregate,
    prove,
    sum_skips_first,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def http_post(url: str, data: dict) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def http_post_raw(url: str, data) -> dict:
    body = json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# PipelineStage base class tests
# ---------------------------------------------------------------------------

class TestPipelineStage(unittest.TestCase):

    def test_base_class_process_passthrough(self):
        stage = PipelineStage()
        records = [{"id": 1}, {"id": 2}]
        result = stage.process(records)
        self.assertEqual(result, records)

    def test_base_class_process_empty(self):
        stage = PipelineStage()
        result = stage.process([])
        self.assertEqual(result, [])

    def test_base_class_repr(self):
        stage = PipelineStage()
        self.assertIn("PipelineStage", repr(stage))

    def test_subclass_can_override_process(self):
        class DoubleStage(PipelineStage):
            def process(self, records):
                return records + records

        stage = DoubleStage()
        records = [{"id": 1}]
        result = stage.process(records)
        self.assertEqual(len(result), 2)


# ---------------------------------------------------------------------------
# SchemaSpec tests
# ---------------------------------------------------------------------------

class TestSchemaSpec(unittest.TestCase):

    def test_basic_field_types(self):
        schema = SchemaSpec({"id": int, "name": str, "score": float})
        self.assertEqual(schema.get_field_type("id"), int)
        self.assertEqual(schema.get_field_type("name"), str)
        self.assertEqual(schema.get_field_type("score"), float)

    def test_string_type_names(self):
        schema = SchemaSpec({"id": "int", "name": "str", "active": "bool"})
        self.assertEqual(schema.get_field_type("id"), int)
        self.assertEqual(schema.get_field_type("name"), str)
        self.assertEqual(schema.get_field_type("active"), bool)

    def test_field_names(self):
        schema = SchemaSpec({"a": int, "b": str})
        self.assertIn("a", schema.field_names())
        self.assertIn("b", schema.field_names())

    def test_is_required_default(self):
        schema = SchemaSpec({"id": int})
        self.assertTrue(schema.is_required("id"))

    def test_missing_field_returns_none(self):
        schema = SchemaSpec({"id": int})
        self.assertIsNone(schema.get_field_type("nonexistent"))

    def test_dict_spec_with_required_false(self):
        schema = SchemaSpec({"id": {"type": int, "required": False}})
        self.assertFalse(schema.is_required("id"))
        self.assertEqual(schema.get_field_type("id"), int)


# ---------------------------------------------------------------------------
# SchemaValidator tests
# ---------------------------------------------------------------------------

class TestSchemaValidator(unittest.TestCase):

    def setUp(self):
        self.schema = SchemaSpec({"id": int, "name": str, "score": float})
        self.validator = SchemaValidator(self.schema)

    def test_valid_records_pass(self):
        records = [{"id": 1, "name": "Alice", "score": 9.5}]
        result = self.validator.process(records)
        self.assertEqual(len(result), 1)
        self.assertFalse(self.validator.has_errors())

    def test_wrong_type_generates_error(self):
        records = [{"id": "not_an_int", "name": "Alice", "score": 9.5}]
        self.validator.process(records)
        self.assertTrue(self.validator.has_errors())
        self.assertGreater(len(self.validator.get_errors()), 0)

    def test_missing_required_field_generates_error(self):
        records = [{"id": 1, "score": 9.5}]  # missing name
        self.validator.process(records)
        self.assertTrue(self.validator.has_errors())

    def test_strict_mode_drops_invalid_records(self):
        strict_validator = SchemaValidator(self.schema, strict=True)
        records = [
            {"id": 1, "name": "Alice", "score": 9.5},
            {"id": "bad", "name": "Bob", "score": 8.0},
        ]
        result = strict_validator.process(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Alice")

    def test_non_strict_keeps_invalid_records(self):
        records = [{"id": "bad", "name": "Alice", "score": 9.5}]
        result = self.validator.process(records)
        self.assertEqual(len(result), 1)  # kept despite error

    def test_null_values_skip_type_check(self):
        records = [{"id": 1, "name": None, "score": 9.5}]
        self.validator.process(records)
        self.assertFalse(self.validator.has_errors())

    def test_errors_reset_on_each_process(self):
        bad_records = [{"id": "bad", "name": "Alice", "score": 9.5}]
        self.validator.process(bad_records)
        self.assertTrue(self.validator.has_errors())
        good_records = [{"id": 1, "name": "Alice", "score": 9.5}]
        self.validator.process(good_records)
        self.assertFalse(self.validator.has_errors())

    def test_multiple_errors_in_one_record(self):
        records = [{"id": "bad", "name": 123, "score": "oops"}]
        self.validator.process(records)
        self.assertGreaterEqual(len(self.validator.get_errors()), 2)

    def test_empty_records_no_errors(self):
        result = self.validator.process([])
        self.assertEqual(result, [])
        self.assertFalse(self.validator.has_errors())


# ---------------------------------------------------------------------------
# NullHandler tests
# ---------------------------------------------------------------------------

class TestNullHandler(unittest.TestCase):

    def test_propagate_keeps_nulls(self):
        handler = NullHandler(strategy=NullStrategy.PROPAGATE)
        records = [{"id": 1, "name": None}]
        result = handler.process(records)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["name"])

    def test_drop_removes_records_with_nulls(self):
        handler = NullHandler(strategy=NullStrategy.DROP)
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": None}]
        result = handler.process(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 1)

    def test_drop_tracks_dropped_count(self):
        handler = NullHandler(strategy=NullStrategy.DROP)
        records = [{"id": 1, "name": None}, {"id": 2, "name": None}]
        handler.process(records)
        self.assertEqual(handler.dropped_count, 2)

    def test_default_replaces_nulls(self):
        handler = NullHandler(
            strategy=NullStrategy.DEFAULT,
            fields=["name"],
            defaults={"name": "Unknown"},
        )
        records = [{"id": 1, "name": None}]
        result = handler.process(records)
        self.assertEqual(result[0]["name"], "Unknown")

    def test_default_only_for_specified_fields(self):
        handler = NullHandler(
            strategy=NullStrategy.DEFAULT,
            fields=["name"],
            defaults={"name": "Unknown"},
        )
        records = [{"id": 1, "name": None, "score": None}]
        result = handler.process(records)
        self.assertEqual(result[0]["name"], "Unknown")
        self.assertIsNone(result[0]["score"])  # score not in fields

    def test_drop_with_specific_fields(self):
        handler = NullHandler(strategy=NullStrategy.DROP, fields=["name"])
        records = [
            {"id": 1, "name": None, "score": 5},
            {"id": 2, "name": "Bob", "score": None},
        ]
        result = handler.process(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], 2)

    def test_empty_records(self):
        handler = NullHandler(strategy=NullStrategy.DROP)
        result = handler.process([])
        self.assertEqual(result, [])

    def test_null_strategy_enum_values(self):
        self.assertEqual(NullStrategy.DROP.value, "drop")
        self.assertEqual(NullStrategy.DEFAULT.value, "default")
        self.assertEqual(NullStrategy.PROPAGATE.value, "propagate")


# ---------------------------------------------------------------------------
# Deduplicator tests
# ---------------------------------------------------------------------------

class TestDeduplicator(unittest.TestCase):

    def test_removes_exact_duplicates(self):
        dedup = Deduplicator(key_fields="id")
        records = [{"id": 1, "name": "Alice"}, {"id": 1, "name": "Alice"}, {"id": 2}]
        result = dedup.process(records)
        self.assertEqual(len(result), 2)

    def test_keeps_first_by_default(self):
        dedup = Deduplicator(key_fields="id", keep="first")
        records = [{"id": 1, "name": "First"}, {"id": 1, "name": "Second"}]
        result = dedup.process(records)
        self.assertEqual(result[0]["name"], "First")

    def test_keeps_last(self):
        dedup = Deduplicator(key_fields="id", keep="last")
        records = [{"id": 1, "name": "First"}, {"id": 1, "name": "Second"}]
        result = dedup.process(records)
        self.assertEqual(result[0]["name"], "Second")

    def test_multi_field_key(self):
        dedup = Deduplicator(key_fields=["first", "last"])
        records = [
            {"first": "John", "last": "Doe", "age": 30},
            {"first": "John", "last": "Doe", "age": 31},
            {"first": "Jane", "last": "Doe", "age": 25},
        ]
        result = dedup.process(records)
        self.assertEqual(len(result), 2)

    def test_duplicate_count_tracked(self):
        dedup = Deduplicator(key_fields="id")
        records = [{"id": 1}, {"id": 1}, {"id": 1}]
        dedup.process(records)
        self.assertEqual(dedup.duplicate_count, 2)

    def test_no_duplicates_unchanged(self):
        dedup = Deduplicator(key_fields="id")
        records = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = dedup.process(records)
        self.assertEqual(len(result), 3)

    def test_empty_records(self):
        dedup = Deduplicator(key_fields="id")
        result = dedup.process([])
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Reconciler tests
# ---------------------------------------------------------------------------

class TestReconciler(unittest.TestCase):

    def test_exact_match(self):
        rec = Reconciler()
        src = [{"id": 1}, {"id": 2}]
        dst = [{"id": 1}, {"id": 2}]
        result = rec.reconcile(src, dst)
        self.assertTrue(result.match)
        self.assertEqual(result.discrepancy, 0)

    def test_count_mismatch(self):
        rec = Reconciler()
        src = [{"id": 1}, {"id": 2}, {"id": 3}]
        dst = [{"id": 1}, {"id": 2}]
        result = rec.reconcile(src, dst)
        self.assertFalse(result.match)
        self.assertEqual(result.discrepancy, 1)

    def test_tolerance_within_range(self):
        rec = Reconciler(tolerance=2)
        src = [{"id": i} for i in range(10)]
        dst = [{"id": i} for i in range(9)]  # 1 less
        result = rec.reconcile(src, dst)
        self.assertTrue(result.match)

    def test_tolerance_exceeded(self):
        rec = Reconciler(tolerance=1)
        src = [{"id": i} for i in range(10)]
        dst = [{"id": i} for i in range(7)]  # 3 less
        result = rec.reconcile(src, dst)
        self.assertFalse(result.match)

    def test_field_value_mismatch_detected(self):
        rec = Reconciler(check_fields=["name"])
        src = [{"id": 1, "name": "Alice"}]
        dst = [{"id": 1, "name": "Alicia"}]  # different name
        result = rec.reconcile(src, dst, key_field="id")
        self.assertEqual(len(result.field_mismatches), 1)
        self.assertEqual(result.field_mismatches[0]["field"], "name")

    def test_no_field_mismatches_when_matching(self):
        rec = Reconciler(check_fields=["name"])
        src = [{"id": 1, "name": "Alice"}]
        dst = [{"id": 1, "name": "Alice"}]
        result = rec.reconcile(src, dst, key_field="id")
        self.assertEqual(len(result.field_mismatches), 0)

    def test_reconciliation_result_repr(self):
        result = ReconciliationResult(10, 10, True, 0)
        self.assertIn("ReconciliationResult", repr(result))

    def test_empty_datasets(self):
        rec = Reconciler()
        result = rec.reconcile([], [])
        self.assertTrue(result.match)
        self.assertEqual(result.source_count, 0)
        self.assertEqual(result.dest_count, 0)


# ---------------------------------------------------------------------------
# Aggregator tests
# ---------------------------------------------------------------------------

class TestAggregator(unittest.TestCase):

    def setUp(self):
        self.records = [
            {"dept": "eng", "salary": 100},
            {"dept": "eng", "salary": 200},
            {"dept": "hr", "salary": 150},
            {"dept": "hr", "salary": 50},
        ]

    def test_sum_aggregation(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"total": (AggregateFunction.SUM, "salary")},
        )
        result = agg.process(self.records)
        totals = {r["dept"]: r["total"] for r in result}
        self.assertEqual(totals["eng"], 300)
        self.assertEqual(totals["hr"], 200)

    def test_count_aggregation(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"count": (AggregateFunction.COUNT, "salary")},
        )
        result = agg.process(self.records)
        counts = {r["dept"]: r["count"] for r in result}
        self.assertEqual(counts["eng"], 2)
        self.assertEqual(counts["hr"], 2)

    def test_avg_aggregation(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"avg_salary": (AggregateFunction.AVG, "salary")},
        )
        result = agg.process(self.records)
        avgs = {r["dept"]: r["avg_salary"] for r in result}
        self.assertEqual(avgs["eng"], 150.0)
        self.assertEqual(avgs["hr"], 100.0)

    def test_min_aggregation(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"min_salary": (AggregateFunction.MIN, "salary")},
        )
        result = agg.process(self.records)
        mins = {r["dept"]: r["min_salary"] for r in result}
        self.assertEqual(mins["eng"], 100)
        self.assertEqual(mins["hr"], 50)

    def test_max_aggregation(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"max_salary": (AggregateFunction.MAX, "salary")},
        )
        result = agg.process(self.records)
        maxs = {r["dept"]: r["max_salary"] for r in result}
        self.assertEqual(maxs["eng"], 200)
        self.assertEqual(maxs["hr"], 150)

    def test_multi_group_by(self):
        records = [
            {"dept": "eng", "level": "senior", "salary": 200},
            {"dept": "eng", "level": "junior", "salary": 100},
            {"dept": "eng", "level": "senior", "salary": 220},
        ]
        agg = Aggregator(
            group_by=["dept", "level"],
            aggregations={"total": (AggregateFunction.SUM, "salary")},
        )
        result = agg.process(records)
        self.assertEqual(len(result), 2)
        senior = next(r for r in result if r["level"] == "senior")
        self.assertEqual(senior["total"], 420)

    def test_empty_records(self):
        agg = Aggregator(
            group_by="dept",
            aggregations={"total": (AggregateFunction.SUM, "salary")},
        )
        result = agg.process([])
        self.assertEqual(result, [])

    def test_aggregate_function_enum_values(self):
        self.assertEqual(AggregateFunction.SUM.value, "sum")
        self.assertEqual(AggregateFunction.COUNT.value, "count")
        self.assertEqual(AggregateFunction.AVG.value, "avg")
        self.assertEqual(AggregateFunction.MIN.value, "min")
        self.assertEqual(AggregateFunction.MAX.value, "max")


# ---------------------------------------------------------------------------
# Joiner tests
# ---------------------------------------------------------------------------

class TestJoiner(unittest.TestCase):

    def setUp(self):
        self.left = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 3, "name": "Carol"},
        ]
        self.right = [
            {"id": 1, "dept": "eng"},
            {"id": 2, "dept": "hr"},
            {"id": 4, "dept": "finance"},
        ]

    def test_inner_join_matches_only(self):
        joiner = Joiner(join_type=JoinType.INNER, key_field="id")
        result = joiner.join(self.left, self.right)
        ids = [r["id"] for r in result]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        self.assertNotIn(3, ids)
        self.assertNotIn(4, ids)

    def test_left_join_keeps_all_left(self):
        joiner = Joiner(join_type=JoinType.LEFT, key_field="id")
        result = joiner.join(self.left, self.right)
        ids = [r["id"] for r in result]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        self.assertIn(3, ids)  # Carol has no match in right but still included
        self.assertNotIn(4, ids)

    def test_right_join_keeps_all_right(self):
        joiner = Joiner(join_type=JoinType.RIGHT, key_field="id")
        result = joiner.join(self.left, self.right)
        ids = [r["id"] for r in result]
        self.assertIn(1, ids)
        self.assertIn(2, ids)
        self.assertNotIn(3, ids)
        self.assertIn(4, ids)  # finance has no match in left but still included

    def test_inner_join_result_has_both_fields(self):
        joiner = Joiner(join_type=JoinType.INNER, key_field="id")
        result = joiner.join(self.left, self.right)
        alice = next(r for r in result if r["id"] == 1)
        self.assertIn("name", alice)
        self.assertIn("dept", alice)

    def test_join_type_enum_values(self):
        self.assertEqual(JoinType.INNER.value, "inner")
        self.assertEqual(JoinType.LEFT.value, "left")
        self.assertEqual(JoinType.RIGHT.value, "right")

    def test_empty_left_inner_join(self):
        joiner = Joiner(join_type=JoinType.INNER, key_field="id")
        result = joiner.join([], self.right)
        self.assertEqual(result, [])

    def test_empty_right_left_join(self):
        joiner = Joiner(join_type=JoinType.LEFT, key_field="id")
        result = joiner.join(self.left, [])
        self.assertEqual(len(result), len(self.left))

    def test_empty_left_right_join(self):
        joiner = Joiner(join_type=JoinType.RIGHT, key_field="id")
        result = joiner.join([], self.right)
        self.assertEqual(len(result), len(self.right))


# ---------------------------------------------------------------------------
# PipelineReport tests
# ---------------------------------------------------------------------------

class TestPipelineReport(unittest.TestCase):

    def test_basic_report_creation(self):
        report = PipelineReport(rows_in=100, rows_out=90, rows_dropped=10, throughput=1000.0)
        self.assertEqual(report.rows_in, 100)
        self.assertEqual(report.rows_out, 90)
        self.assertEqual(report.rows_dropped, 10)
        self.assertEqual(report.throughput, 1000.0)

    def test_report_repr(self):
        report = PipelineReport(rows_in=10, rows_out=8, rows_dropped=2)
        r = repr(report)
        self.assertIn("PipelineReport", r)

    def test_report_to_dict(self):
        report = PipelineReport(rows_in=10, rows_out=8, rows_dropped=2, throughput=500.0)
        d = report.to_dict()
        self.assertIn("rows_in", d)
        self.assertIn("rows_out", d)
        self.assertIn("rows_dropped", d)
        self.assertIn("throughput", d)

    def test_report_errors_list(self):
        errors = [{"index": 0, "error": "bad field"}]
        report = PipelineReport(errors=errors)
        self.assertEqual(len(report.errors), 1)

    def test_report_default_values(self):
        report = PipelineReport()
        self.assertEqual(report.rows_in, 0)
        self.assertEqual(report.rows_out, 0)
        self.assertEqual(report.rows_dropped, 0)
        self.assertEqual(report.errors, [])
        self.assertEqual(report.throughput, 0.0)


# ---------------------------------------------------------------------------
# PipelineRunner tests
# ---------------------------------------------------------------------------

class TestPipelineRunner(unittest.TestCase):

    def test_empty_pipeline_passthrough(self):
        runner = PipelineRunner()
        records = [{"id": 1}, {"id": 2}]
        report = runner.run(records)
        self.assertEqual(report.rows_in, 2)
        self.assertEqual(report.rows_out, 2)

    def test_single_stage_pipeline(self):
        dedup = Deduplicator(key_fields="id")
        runner = PipelineRunner(stages=[dedup])
        records = [{"id": 1}, {"id": 1}, {"id": 2}]
        report = runner.run(records)
        self.assertEqual(report.rows_in, 3)
        self.assertEqual(report.rows_out, 2)
        self.assertEqual(report.rows_dropped, 1)

    def test_multi_stage_pipeline(self):
        schema = SchemaSpec({"id": int, "name": str})
        validator = SchemaValidator(schema, strict=True)
        dedup = Deduplicator(key_fields="id")
        runner = PipelineRunner(stages=[validator, dedup])
        records = [
            {"id": 1, "name": "Alice"},
            {"id": 1, "name": "Alice"},  # duplicate
            {"id": "bad", "name": "Bob"},  # invalid
        ]
        report = runner.run(records)
        self.assertEqual(report.rows_in, 3)
        self.assertLess(report.rows_out, 3)

    def test_throughput_is_positive(self):
        runner = PipelineRunner()
        records = [{"id": i} for i in range(100)]
        report = runner.run(records)
        self.assertGreater(report.throughput, 0)

    def test_add_stage_method(self):
        runner = PipelineRunner()
        dedup = Deduplicator(key_fields="id")
        returned = runner.add_stage(dedup)
        self.assertEqual(returned, runner)
        self.assertEqual(len(runner.stages), 1)

    def test_get_last_report(self):
        runner = PipelineRunner()
        self.assertIsNone(runner.get_last_report())
        runner.run([{"id": 1}])
        self.assertIsNotNone(runner.get_last_report())

    def test_clear_stages(self):
        runner = PipelineRunner(stages=[Deduplicator("id")])
        runner.clear_stages()
        self.assertEqual(len(runner.stages), 0)

    def test_validator_errors_propagated_to_report(self):
        schema = SchemaSpec({"id": int})
        validator = SchemaValidator(schema)
        runner = PipelineRunner(stages=[validator])
        records = [{"id": "not_int"}]
        report = runner.run(records)
        self.assertGreater(len(report.errors), 0)

    def test_stage_reports_collected(self):
        runner = PipelineRunner(stages=[PipelineStage(), PipelineStage()])
        report = runner.run([{"id": 1}])
        self.assertEqual(len(report.stage_reports), 2)

    def test_null_handler_in_pipeline(self):
        handler = NullHandler(strategy=NullStrategy.DROP)
        runner = PipelineRunner(stages=[handler])
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": None}]
        report = runner.run(records)
        self.assertEqual(report.rows_out, 1)


# ---------------------------------------------------------------------------
# MockPipelineServer tests
# ---------------------------------------------------------------------------

class TestMockPipelineServer(unittest.TestCase):

    def setUp(self):
        self.server = MockPipelineServer(port=0)
        self.server.start()
        self.base_url = self.server.get_url()

    def tearDown(self):
        self.server.stop()

    def test_health_endpoint(self):
        data = http_get(f"{self.base_url}/health")
        self.assertEqual(data["status"], "ok")

    def test_post_records_and_get(self):
        records = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        resp = http_post_raw(f"{self.base_url}/records", records)
        self.assertEqual(resp["status"], "ok")
        self.assertEqual(resp["count"], 2)

        data = http_get(f"{self.base_url}/records")
        self.assertEqual(len(data["records"]), 2)

    def test_post_records_as_dict_with_records_key(self):
        payload = {"records": [{"id": 1}]}
        resp = http_post(f"{self.base_url}/records", payload)
        self.assertEqual(resp["status"], "ok")

    def test_get_records_empty_initially(self):
        data = http_get(f"{self.base_url}/records")
        self.assertEqual(data["records"], [])

    def test_post_report(self):
        report_data = {"rows_in": 10, "rows_out": 8, "rows_dropped": 2}
        resp = http_post(f"{self.base_url}/report", report_data)
        self.assertEqual(resp["status"], "ok")

    def test_get_report_after_post(self):
        report_data = {"rows_in": 10, "rows_out": 8}
        http_post(f"{self.base_url}/report", report_data)
        data = http_get(f"{self.base_url}/report")
        self.assertEqual(data["rows_in"], 10)

    def test_get_report_404_when_empty(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            http_get(f"{self.base_url}/report")
        self.assertEqual(ctx.exception.code, 404)

    def test_stats_endpoint(self):
        data = http_get(f"{self.base_url}/stats")
        self.assertIn("record_count", data)
        self.assertIn("has_report", data)

    def test_run_endpoint(self):
        payload = {"records": [{"id": 1}, {"id": 2}]}
        resp = http_post(f"{self.base_url}/run", payload)
        self.assertIn("rows_in", resp)
        self.assertEqual(resp["rows_in"], 2)

    def test_clear_endpoint(self):
        records = [{"id": 1}]
        http_post_raw(f"{self.base_url}/records", records)
        resp = http_post(f"{self.base_url}/clear", {})
        self.assertEqual(resp["status"], "cleared")
        data = http_get(f"{self.base_url}/records")
        self.assertEqual(data["records"], [])

    def test_404_for_unknown_path(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            http_get(f"{self.base_url}/unknown_path")
        self.assertEqual(ctx.exception.code, 404)

    def test_context_manager(self):
        with MockPipelineServer(port=0) as srv:
            url = srv.get_url()
            data = http_get(f"{url}/health")
            self.assertEqual(data["status"], "ok")

    def test_server_port_is_positive(self):
        self.assertGreater(self.server.port, 0)

    def test_default_port_constant(self):
        self.assertEqual(DEFAULT_PORT, 19050)

    def test_stats_after_posting_records(self):
        records = [{"id": i} for i in range(5)]
        http_post_raw(f"{self.base_url}/records", records)
        data = http_get(f"{self.base_url}/stats")
        self.assertEqual(data["record_count"], 5)


# ---------------------------------------------------------------------------
# Integration: full pipeline test
# ---------------------------------------------------------------------------

class TestFullPipeline(unittest.TestCase):

    def test_full_etl_pipeline(self):
        """Test a realistic ETL pipeline: validate -> null handle -> dedup -> aggregate."""
        raw_records = [
            {"id": 1, "dept": "eng", "salary": 100},
            {"id": 2, "dept": "eng", "salary": 200},
            {"id": 2, "dept": "eng", "salary": 200},  # duplicate
            {"id": 3, "dept": "hr", "salary": None},  # null
            {"id": 4, "dept": "hr", "salary": 150},
            {"id": "x", "dept": "hr", "salary": 75},  # invalid id type
        ]

        schema = SchemaSpec({"id": int, "dept": str, "salary": int})
        validator = SchemaValidator(schema, strict=True)
        null_handler = NullHandler(strategy=NullStrategy.DROP)
        dedup = Deduplicator(key_fields="id")
        agg = Aggregator(
            group_by="dept",
            aggregations={"total": (AggregateFunction.SUM, "salary"), "count": (AggregateFunction.COUNT, "salary")},
        )

        runner = PipelineRunner(stages=[validator, null_handler, dedup, agg])
        report = runner.run(raw_records)

        self.assertGreater(report.rows_in, 0)
        self.assertGreater(report.throughput, 0)
        self.assertIsNotNone(runner.get_last_report())

    def test_pipeline_with_join(self):
        """Test pipeline with join operation."""
        employees = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
        ]
        departments = [
            {"id": 1, "dept": "eng"},
            {"id": 2, "dept": "hr"},
        ]
        joiner = Joiner(join_type=JoinType.INNER, key_field="id")
        joined = joiner.join(employees, departments)
        self.assertEqual(len(joined), 2)
        alice = next(r for r in joined if r.get("id") == 1)
        self.assertEqual(alice.get("name"), "Alice")
        self.assertEqual(alice.get("dept"), "eng")

    def test_reconciler_after_dedup(self):
        """Test reconciler verifies row counts after dedup."""
        source = [{"id": 1}, {"id": 2}, {"id": 2}]
        dedup = Deduplicator(key_fields="id")
        dest = dedup.process(source)

        rec = Reconciler()
        result = rec.reconcile(source, dest)
        self.assertFalse(result.match)
        self.assertEqual(result.discrepancy, 1)


# ---------------------------------------------------------------------------
# TEETH: the group-aggregator oracle vs. its planted mutants
# ---------------------------------------------------------------------------

class TestTeeth(unittest.TestCase):
    """Assert the harness's teeth: the correct group aggregator is NOT flagged,
    every planted aggregation mutant IS caught, the frozen corpus is non-empty,
    and the expectations are non-circular (literals, not oracle-derived)."""

    def _records(self):
        # Defensive copy so a buggy impl mutating its input cannot bleed across.
        return [dict(r) for r in AGG_RECORDS]

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(len(AGG_CORPUS), 1)
        self.assertEqual(TEETH.corpus_size, len(AGG_CORPUS))

    def test_prove_oracle_is_false(self):
        # The correct oracle must reproduce every frozen literal -> not caught.
        self.assertIs(prove(oracle_aggregate), False)

    def test_prove_each_mutant_is_true(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertIs(prove(mutant.impl), True)

    def test_mutants_are_the_two_planted_aggregation_bugs(self):
        names = {m.name for m in TEETH.mutants}
        self.assertEqual(names, {"avg_div_by_groupcount", "sum_skips_first"})

    def test_avg_div_by_groupcount_caught_on_unequal_groups(self):
        # eng has 3 values == 3 groups, so its AVG coincides (200.0); the bug is
        # caught because hr (200/3) and ops (90/3) diverge from the true AVG.
        out = {g.key: g for g in avg_div_by_groupcount(self._records())}
        self.assertAlmostEqual(out["hr"].avg, 200 / 3)
        self.assertAlmostEqual(out["ops"].avg, 90 / 3)
        self.assertNotAlmostEqual(out["hr"].avg, 100.0)
        self.assertNotAlmostEqual(out["ops"].avg, 90.0)
        self.assertIs(prove(avg_div_by_groupcount), True)

    def test_sum_skips_first_drops_first_value_per_group(self):
        out = {g.key: g for g in sum_skips_first(self._records())}
        self.assertEqual(out["eng"].total, 500)   # 200 + 300 (dropped 100)
        self.assertEqual(out["hr"].total, 50)      # 50 (dropped 150)
        self.assertEqual(out["ops"].total, 0)      # dropped its only value
        self.assertIs(prove(sum_skips_first), True)

    def test_oracle_matches_every_frozen_literal(self):
        produced = {g.key: g for g in oracle_aggregate(self._records())}
        self.assertEqual(set(produced), {g.key for g in AGG_CORPUS})
        for expected in AGG_CORPUS:
            got = produced[expected.key]
            self.assertEqual(got.count, expected.count)
            self.assertEqual(got.total, expected.total)
            self.assertAlmostEqual(got.avg, expected.avg)

    def test_noncircular_flipping_a_literal_flags_the_oracle(self):
        # The campaign-critical property: expectations are frozen LITERALS, not
        # derived from the oracle at runtime. If we flip one literal, the correct
        # oracle must now be "caught" (prove -> True). This is exactly the check
        # the gate cannot perform for us.
        import dataclasses

        original = pipeline_mod.AGG_CORPUS
        self.addCleanup(setattr, pipeline_mod, "AGG_CORPUS", original)
        flipped = tuple(
            dataclasses.replace(g, avg=g.avg + 1.0) if g.key == "hr" else g
            for g in original
        )
        pipeline_mod.AGG_CORPUS = flipped
        self.assertIs(prove(oracle_aggregate), True)

    def test_teeth_metadata(self):
        self.assertEqual(TEETH.kind, "oracle_swap")
        self.assertGreaterEqual(len(TEETH.mutants), 1)
        self.assertIs(TEETH.oracle, oracle_aggregate)
        self.assertIs(TEETH.prove, prove)


if __name__ == "__main__":
    unittest.main(verbosity=2)
