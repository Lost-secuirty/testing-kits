"""Test suite for grpc_contract_test_harness."""

import unittest

from harnesses.core.grpc_contract_test_harness import (
    ASYMMETRIC_RPC,
    CLOSED_ENUM,
    INBOUND_MD,
    OPEN_ENUM,
    RPCS,
    SCENARIOS,
    STATUS_CODES,
    V1,
    V2_GOOD,
    V2_REUSE,
    V2_WIRE,
    GrpcReport,
    WireField,
    _run_self_test,
    audit,
    enum_accessor_buggy,
    enum_accessor_oracle,
    idempotency_handler_ignoring,
    list_scenarios,
    metadata_hop_dropping,
    propagate_deadline_ignoring,
    propagate_deadline_oracle,
    roundtrip,
    status_service_misuse,
    stream_handler_non_cancelling,
    validate_evolution,
)


class TestStatusCodes(unittest.TestCase):
    def test_seventeen_canonical_codes(self):
        self.assertEqual(len(STATUS_CODES), 17)
        self.assertEqual(STATUS_CODES["OK"], 0)
        self.assertEqual(STATUS_CODES["UNAUTHENTICATED"], 16)

    def test_resource_exhausted_distinct(self):
        self.assertNotEqual(STATUS_CODES["RESOURCE_EXHAUSTED"],
                            STATUS_CODES["PERMISSION_DENIED"])


class TestEvolution(unittest.TestCase):
    def test_good_evolution_clean(self):
        self.assertEqual(validate_evolution(V1, V2_GOOD), (0, 0, 0))

    def test_reuse_flagged(self):
        reuse, wire, unreserved = validate_evolution(V1, V2_REUSE)
        self.assertGreaterEqual(reuse, 1)

    def test_wire_change_flagged(self):
        reuse, wire, unreserved = validate_evolution(V1, V2_WIRE)
        self.assertGreaterEqual(wire, 1)

    def test_reserved_blocks_pollution(self):
        old = [WireField(2, "varint", 42)]
        _, unknown_good = roundtrip(old, V2_GOOD)
        known_reuse, _ = roundtrip(old, V2_REUSE)
        self.assertEqual(len(unknown_good), 1)   # reserved -> safe
        self.assertEqual(len(known_reuse), 1)    # reused -> polluted


class TestEnum(unittest.TestCase):
    def test_open_returns_actual(self):
        self.assertEqual(enum_accessor_oracle(OPEN_ENUM, 99), (99, False))

    def test_closed_returns_default_unknown(self):
        self.assertEqual(enum_accessor_oracle(CLOSED_ENUM, 99), (0, True))

    def test_buggy_treats_closed_as_open(self):
        self.assertEqual(enum_accessor_buggy(CLOSED_ENUM, 99), (99, False))


class TestDeadline(unittest.TestCase):
    def test_oracle_decreases(self):
        self.assertEqual(propagate_deadline_oracle(100, 30), 70)

    def test_ignoring_does_not(self):
        self.assertEqual(propagate_deadline_ignoring(100, 30), 100)


class TestSizeLimits(unittest.TestCase):
    def test_corpus_symmetric(self):
        self.assertTrue(all(r.send_limit <= r.recv_limit for r in RPCS))

    def test_asymmetric_rpc_is_asymmetric(self):
        self.assertGreater(ASYMMETRIC_RPC.send_limit, ASYMMETRIC_RPC.recv_limit)


class TestAuditOracle(unittest.TestCase):
    def test_oracle_clean(self):
        rep = audit()
        self.assertTrue(rep.meets_contract())
        self.assertEqual(rep.total_violations, 0)


class TestBuggyImplsCaught(unittest.TestCase):
    def test_field_reuse(self):
        self.assertGreaterEqual(audit(evolved_desc=V2_REUSE).roundtrip_pollution, 1)

    def test_wire_change(self):
        self.assertGreaterEqual(audit(evolved_desc=V2_WIRE).wire_type_changes, 1)

    def test_closed_enum_misread(self):
        self.assertGreaterEqual(audit(enum_accessor=enum_accessor_buggy).enum_mishandled, 1)

    def test_deadline_ignored(self):
        self.assertGreaterEqual(
            audit(deadline_propagator=propagate_deadline_ignoring).deadline_violations, 1)

    def test_stream_not_cancelled(self):
        self.assertGreaterEqual(
            audit(stream_handler=stream_handler_non_cancelling).stream_overruns, 1)

    def test_status_misuse(self):
        self.assertGreaterEqual(audit(status_service=status_service_misuse).status_misuses, 1)

    def test_metadata_drop(self):
        self.assertGreaterEqual(audit(metadata_hop=metadata_hop_dropping).metadata_drops, 1)

    def test_size_asymmetry(self):
        self.assertGreaterEqual(audit(rpcs=[ASYMMETRIC_RPC]).size_asymmetries, 1)

    def test_idempotency_breach(self):
        self.assertGreaterEqual(
            audit(idempotency_handler=idempotency_handler_ignoring).idempotency_breaches, 1)


class TestReportLogic(unittest.TestCase):
    def test_total_and_meets_contract(self):
        clean = GrpcReport(0, 0, 0, 0, 0, 0, 0, 0, 0)
        self.assertEqual(clean.total_violations, 0)
        self.assertTrue(clean.meets_contract())
        dirty = GrpcReport(1, 0, 0, 0, 0, 0, 0, 0, 0)
        self.assertEqual(dirty.total_violations, 1)
        self.assertFalse(dirty.meets_contract())


class TestSelfTest(unittest.TestCase):
    def test_at_least_20_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 20)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
