"""Test suite for queue_test_harness."""

import unittest

from harnesses.core.queue_test_harness import (
    SCENARIOS,
    Clock,
    Delivery,
    DeliveryReport,
    InMemoryBroker,
    LossyExactlyOnce,
    Message,
    NaiveBroker,
    NoDlqBroker,
    OrderBreakingRebalance,
    QueueConfig,
    _run_self_test,
    build_report,
    consume_all,
    list_scenarios,
)


def _broker(delivery=Delivery.AT_LEAST_ONCE, **cfg):
    return InMemoryBroker(QueueConfig(delivery=delivery, **cfg), Clock())


class TestMessageAndConfig(unittest.TestCase):
    def test_message_is_frozen(self):
        m = Message("a", "k", "body")
        with self.assertRaises(Exception):
            m.id = "b"  # type: ignore[misc]

    def test_config_defaults(self):
        c = QueueConfig()
        self.assertEqual(c.max_deliveries, 3)
        self.assertEqual(c.delivery, Delivery.AT_LEAST_ONCE)


class TestOracleBroker(unittest.TestCase):
    def test_publish_then_process_acks_once(self):
        b = _broker()
        b.publish(Message("m1", "k"))
        proc = consume_all(b)
        self.assertEqual([m.id for m in proc], ["m1"])
        self.assertEqual(b.deliveries_of("m1"), 1)

    def test_exactly_once_dedup_on_redelivery(self):
        b = _broker(Delivery.EXACTLY_ONCE)
        b.publish(Message("m1", "k"))
        rep = build_report(b, consume_all(b, lose_ack_once=("m1",)))
        self.assertTrue(rep.is_exactly_once)
        self.assertEqual(rep.processed_unique, 1)

    def test_at_least_once_duplicates_on_lost_ack(self):
        b = _broker(Delivery.AT_LEAST_ONCE)
        b.publish(Message("m1", "k"))
        rep = build_report(b, consume_all(b, lose_ack_once=("m1",)))
        self.assertGreaterEqual(rep.duplicates, 1)

    def test_dlq_after_max_deliveries(self):
        b = _broker(max_deliveries=3)
        b.publish(Message("poison", "k"))
        consume_all(b, nack_always=("poison",))
        self.assertEqual([m.id for m in b.dlq], ["poison"])

    def test_fifo_within_key(self):
        b = _broker()
        for i in range(4):
            b.publish(Message(f"m{i}", "same"))
        proc = consume_all(b)
        self.assertEqual([m.id for m in proc], ["m0", "m1", "m2", "m3"])
        self.assertTrue(build_report(b, proc).ordering_preserved)

    def test_head_of_key_blocks_second_until_acked(self):
        b = _broker()
        b.publish(Message("m0", "K"))
        b.publish(Message("m1", "K"))
        b.poll()  # m0 in flight
        self.assertIsNone(b.poll())  # m1 blocked

    def test_backpressure_caps_in_flight(self):
        b = _broker(max_in_flight=2)
        for i in range(3):
            b.publish(Message(f"m{i}", f"k{i}"))
        b.poll()
        b.poll()
        self.assertIsNone(b.poll())
        self.assertLessEqual(b.max_in_flight_observed, 2)

    def test_backpressure_rejects_publish_over_depth(self):
        b = _broker(max_queue_depth=1)
        self.assertTrue(b.publish(Message("m0", "a")))
        self.assertFalse(b.publish(Message("m1", "b")))


class TestBuggyImplsCaught(unittest.TestCase):
    def test_naive_broker_loses_on_crash(self):
        b = NaiveBroker(QueueConfig(), Clock())
        b.publish(Message("m1", "k"))
        proc = consume_all(b, crash_once=("m1",))
        self.assertNotIn("m1", {m.id for m in proc})

    def test_lossy_exactly_once_double_processes(self):
        b = LossyExactlyOnce(QueueConfig(delivery=Delivery.EXACTLY_ONCE), Clock())
        b.publish(Message("m1", "k"))
        rep = build_report(b, consume_all(b, lose_ack_once=("m1",)))
        self.assertGreaterEqual(rep.duplicates, 1)

    def test_order_breaking_rebalance_delivers_non_head(self):
        b = OrderBreakingRebalance(QueueConfig(), Clock())
        b.publish(Message("m0", "K"))
        b.publish(Message("m1", "K"))
        b.poll()
        self.assertIsNotNone(b.poll())  # bug: second same-key msg delivered

    def test_no_dlq_never_routes(self):
        b = NoDlqBroker(QueueConfig(max_deliveries=2), Clock())
        b.publish(Message("poison", "k"))
        for _ in range(5):
            r = b.poll()
            if r:
                b.nack(r)
        self.assertEqual(len(b.dlq), 0)
        self.assertGreater(b.deliveries_of("poison"), 2)


class TestDeliveryReport(unittest.TestCase):
    def test_props(self):
        r = DeliveryReport(3, 3, 3, 0, 3, 0, 0, 2)
        self.assertTrue(r.is_exactly_once)
        self.assertTrue(r.ordering_preserved)
        self.assertFalse(DeliveryReport(1, 2, 1, 1, 1, 0, 1, 1).is_exactly_once)
        self.assertFalse(DeliveryReport(1, 2, 1, 1, 1, 0, 1, 1).ordering_preserved)


class TestSelfTest(unittest.TestCase):
    def test_at_least_18_scenarios(self):
        self.assertGreaterEqual(len(list_scenarios()), 18)
        self.assertEqual(len(SCENARIOS), len(list_scenarios()))

    def test_self_test_passes(self):
        self.assertEqual(_run_self_test(), 0)


if __name__ == "__main__":
    unittest.main()
