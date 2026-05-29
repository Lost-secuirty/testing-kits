"""Test suite for graphql_test_harness."""

import unittest

from harnesses.core.graphql_test_harness import (
    ABUSIVE_QUERIES,
    FRAGMENTS_ACYCLIC,
    FRAGMENTS_DIRECT_CYCLE,
    FRAGMENTS_INDIRECT_CYCLE,
    LEAKY_RESOLVERS,
    RESOLVERS_FULL,
    SCENARIOS,
    SCHEMA,
    GraphQLConfig,
    GraphQLParseError,
    GraphQLReport,
    QueryTooCostly,
    QueryTooDeep,
    UnknownField,
    UnknownFragment,
    _run_self_test,
    audit,
    detect_n_plus_one,
    enforce_limits,
    fragment_cycles,
    list_scenarios,
    parse_query,
    query_cost,
    query_depth,
    schema_resolver_coverage,
    validate_fields,
    validate_fragments,
)


class TestSchemaFixture(unittest.TestCase):
    def test_resolvers_full_covers_schema(self):
        missing, orphan = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL)
        self.assertEqual(missing, [])
        self.assertEqual(orphan, [])

    def test_schema_has_list_and_scalar_fields(self):
        self.assertTrue(SCHEMA.lookup("Query", "users").is_list)
        self.assertFalse(SCHEMA.lookup("User", "id").is_list)


class TestParser(unittest.TestCase):
    def test_parses_nested_selection(self):
        node = parse_query("{ user { id posts { title } } }")
        self.assertEqual(node.children[0].name, "user")
        self.assertEqual(node.children[0].children[0].name, "id")

    def test_alias_recorded(self):
        node = parse_query("{ a:user { id } }")
        self.assertEqual(node.children[0].alias, "a")
        self.assertEqual(node.children[0].name, "user")

    def test_args_are_skipped(self):
        node = parse_query("{ user(id: 1) { id } }")
        self.assertEqual(node.children[0].name, "user")
        self.assertEqual(node.children[0].children[0].name, "id")

    def test_fragment_spread_parsed(self):
        node = parse_query("{ user { ...userFields } }")
        self.assertTrue(node.children[0].children[0].is_fragment_spread)

    def test_malformed_raises(self):
        with self.assertRaises(GraphQLParseError):
            parse_query("{ user { id ")  # unbalanced


class TestCoverageOracle(unittest.TestCase):
    def test_missing_detected(self):
        missing, _ = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL - {("User", "name")})
        self.assertIn(("User", "name"), missing)

    def test_orphan_detected(self):
        _, orphan = schema_resolver_coverage(SCHEMA, RESOLVERS_FULL | {("X", "y")})
        self.assertIn(("X", "y"), orphan)


class TestCostAndDepth(unittest.TestCase):
    def test_depth(self):
        self.assertEqual(query_depth(parse_query("{ user { id } }")), 2)

    def test_list_multiplies(self):
        cfg = GraphQLConfig()
        self.assertEqual(query_cost(parse_query("{ users { id } }"), SCHEMA, cfg),
                         cfg.list_field_cost * cfg.scalar_cost)

    def test_nested_list_compounds(self):
        cfg = GraphQLConfig()
        single = query_cost(parse_query("{ users { id } }"), SCHEMA, cfg)
        nested = query_cost(parse_query("{ users { posts { id } } }"), SCHEMA, cfg)
        self.assertGreater(nested, single)

    def test_aliases_counted(self):
        cfg = GraphQLConfig()
        one = query_cost(parse_query("{ user { id } }"), SCHEMA, cfg)
        two = query_cost(parse_query("{ a:user { id } b:user { id } }"), SCHEMA, cfg)
        self.assertEqual(two, 2 * one)

    def test_enforce_rejects_deep(self):
        with self.assertRaises(QueryTooDeep):
            enforce_limits(parse_query(ABUSIVE_QUERIES["deep"]), SCHEMA, GraphQLConfig())

    def test_enforce_rejects_costly(self):
        with self.assertRaises(QueryTooCostly):
            enforce_limits(parse_query(ABUSIVE_QUERIES["costly"]), SCHEMA, GraphQLConfig())

    def test_enforce_accepts_small(self):
        enforce_limits(parse_query("{ user { id name } }"), SCHEMA, GraphQLConfig())


class TestNPlusOne(unittest.TestCase):
    def test_detected(self):
        found = detect_n_plus_one(parse_query("{ users { posts { id } } }"), SCHEMA)
        self.assertIn(("User", "posts"), found)

    def test_batched_excluded(self):
        found = detect_n_plus_one(parse_query("{ users { posts { id } } }"), SCHEMA,
                                  batched=frozenset({("User", "posts")}))
        self.assertEqual(found, [])


class TestFragmentCycles(unittest.TestCase):
    def test_acyclic_zero(self):
        self.assertEqual(fragment_cycles(FRAGMENTS_ACYCLIC), 0)

    def test_direct_cycle(self):
        self.assertGreaterEqual(fragment_cycles(FRAGMENTS_DIRECT_CYCLE), 1)

    def test_indirect_cycle(self):
        self.assertGreaterEqual(fragment_cycles(FRAGMENTS_INDIRECT_CYCLE), 1)


class TestValidation(unittest.TestCase):
    def test_unknown_field(self):
        with self.assertRaises(UnknownField):
            validate_fields(parse_query("{ user { nope } }"), SCHEMA)

    def test_unknown_fragment(self):
        with self.assertRaises(UnknownFragment):
            validate_fragments(parse_query("{ user { ...ghost } }"), FRAGMENTS_ACYCLIC)


class TestBuggyImplsCaught(unittest.TestCase):
    def test_leaky_resolver_set(self):
        missing, orphan = schema_resolver_coverage(SCHEMA, LEAKY_RESOLVERS)
        self.assertGreaterEqual(len(missing), 1)
        self.assertGreaterEqual(len(orphan), 1)


class TestAuditReport(unittest.TestCase):
    def test_full_audit_is_covered_and_rejects_abusive(self):
        rep = audit(SCHEMA, RESOLVERS_FULL, list(ABUSIVE_QUERIES.values()),
                    GraphQLConfig(), FRAGMENTS_ACYCLIC)
        self.assertTrue(rep.fully_covered)
        self.assertEqual(rep.rejected_queries, len(ABUSIVE_QUERIES))
        self.assertEqual(rep.fragment_cycles, 0)

    def test_report_fully_covered_property(self):
        self.assertTrue(GraphQLReport(11, 11, 0, 0, 2, 2, 0, 0, 0).fully_covered)
        self.assertFalse(GraphQLReport(11, 10, 1, 0, 2, 2, 0, 0, 0).fully_covered)


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
