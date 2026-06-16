"""
~120 tests for webhook_test_harness.py
Pure stdlib, zero external dependencies.
"""

import json
import threading
import time
import unittest
import uuid

from harnesses.core.webhook_test_harness import (
    SIG_CORPUS,
    TEETH,
    Clock,
    DeadLetterQueue,
    DeliveryAttempt,
    DeliveryReport,
    DeliveryResult,
    FakeClock,
    MockWebhookHandler,
    RetryConfig,
    SequenceTracker,
    SignatureValidator,
    WebhookEvent,
    WebhookReport,
    WebhookSender,
    list_scenarios,
    off_by_one_tolerance,
    oracle_validate,
    prove,
    sign,
    skips_replay_check,
    verify,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_event(
    event_type: str = "order.created",
    payload: dict | None = None,
    timestamp: float = 1000.0,
    seq: int | None = None,
) -> WebhookEvent:
    return WebhookEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        payload=payload or {"key": "value"},
        timestamp=timestamp,
        sequence_number=seq,
    )


class NoSleepSender(WebhookSender):
    """WebhookSender that records sleeps without actually sleeping."""

    def __init__(self, *args, **kwargs):
        self.sleep_calls: list[float] = []
        super().__init__(*args, sleep_fn=self._record_sleep, **kwargs)

    def _record_sleep(self, secs: float) -> None:
        self.sleep_calls.append(secs)


# ===========================================================================
# 1. sign / verify
# ===========================================================================

class TestSign(unittest.TestCase):

    def test_returns_hex_string(self):
        result = sign(b"hello", "secret")
        self.assertIsInstance(result, str)
        self.assertTrue(all(c in "0123456789abcdef" for c in result))

    def test_length_64_chars(self):
        self.assertEqual(len(sign(b"data", "secret")), 64)

    def test_different_payloads_different_sigs(self):
        self.assertNotEqual(sign(b"a", "s"), sign(b"b", "s"))

    def test_different_secrets_different_sigs(self):
        self.assertNotEqual(sign(b"data", "s1"), sign(b"data", "s2"))

    def test_empty_payload(self):
        result = sign(b"", "secret")
        self.assertEqual(len(result), 64)

    def test_empty_secret(self):
        result = sign(b"data", "")
        self.assertEqual(len(result), 64)

    def test_deterministic(self):
        self.assertEqual(sign(b"hello", "secret"), sign(b"hello", "secret"))

    def test_unicode_secret(self):
        result = sign(b"data", "sécret")
        self.assertEqual(len(result), 64)

    def test_binary_payload(self):
        result = sign(bytes(range(256)), "secret")
        self.assertEqual(len(result), 64)


class TestVerify(unittest.TestCase):

    def test_valid_signature_accepted(self):
        payload = b"hello world"
        secret = "mysecret"
        sig = sign(payload, secret)
        self.assertTrue(verify(payload, secret, sig))

    def test_tampered_body_rejected(self):
        payload = b"original"
        secret = "mysecret"
        sig = sign(payload, secret)
        self.assertFalse(verify(b"tampered", secret, sig))

    def test_wrong_secret_rejected(self):
        payload = b"data"
        sig = sign(payload, "correct")
        self.assertFalse(verify(payload, "wrong", sig))

    def test_empty_sig_rejected(self):
        self.assertFalse(verify(b"data", "secret", ""))

    def test_none_sig_rejected(self):
        self.assertFalse(verify(b"data", "secret", None))  # type: ignore

    def test_malformed_sig_rejected(self):
        self.assertFalse(verify(b"data", "secret", "not-hex-at-all!!!"))

    def test_truncated_sig_rejected(self):
        payload = b"data"
        sig = sign(payload, "secret")[:32]
        self.assertFalse(verify(payload, "secret", sig))

    def test_uppercase_sig_rejected(self):
        # hmac.compare_digest is case-sensitive; uppercase != lowercase hex
        payload = b"data"
        sig = sign(payload, "secret").upper()
        self.assertFalse(verify(payload, "secret", sig))

    def test_flipped_bit_rejected(self):
        payload = b"data"
        secret = "secret"
        sig = sign(payload, secret)
        bad = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        self.assertFalse(verify(payload, secret, bad))

    def test_empty_payload_valid(self):
        sig = sign(b"", "secret")
        self.assertTrue(verify(b"", "secret", sig))


# ===========================================================================
# 2. WebhookEvent
# ===========================================================================

class TestWebhookEvent(unittest.TestCase):

    def test_fields_stored(self):
        e = make_event(event_type="payment.completed", timestamp=999.0)
        self.assertEqual(e.event_type, "payment.completed")
        self.assertEqual(e.timestamp, 999.0)

    def test_to_bytes_returns_bytes(self):
        e = make_event()
        self.assertIsInstance(e.to_bytes(), bytes)

    def test_to_bytes_json_decodable(self):
        e = make_event(payload={"amount": 42})
        data = json.loads(e.to_bytes())
        self.assertEqual(data["payload"]["amount"], 42)

    def test_to_bytes_includes_event_id(self):
        e = make_event()
        data = json.loads(e.to_bytes())
        self.assertEqual(data["event_id"], e.event_id)

    def test_to_bytes_includes_event_type(self):
        e = make_event(event_type="refund.issued")
        data = json.loads(e.to_bytes())
        self.assertEqual(data["event_type"], "refund.issued")

    def test_to_bytes_includes_timestamp(self):
        e = make_event(timestamp=12345.0)
        data = json.loads(e.to_bytes())
        self.assertEqual(data["timestamp"], 12345.0)

    def test_to_bytes_includes_sequence_number_when_set(self):
        e = make_event(seq=7)
        data = json.loads(e.to_bytes())
        self.assertEqual(data["sequence_number"], 7)

    def test_to_bytes_excludes_seq_when_none(self):
        e = make_event(seq=None)
        data = json.loads(e.to_bytes())
        self.assertNotIn("sequence_number", data)

    def test_to_bytes_deterministic(self):
        e = make_event(payload={"z": 1, "a": 2})
        self.assertEqual(e.to_bytes(), e.to_bytes())

    def test_default_sequence_number_none(self):
        e = make_event()
        self.assertIsNone(e.sequence_number)


# ===========================================================================
# 3. DeliveryAttempt / DeliveryResult / DeliveryReport
# ===========================================================================

class TestDeliveryAttempt(unittest.TestCase):

    def test_success_flag(self):
        a = DeliveryAttempt(1, 0.0, 200, True)
        self.assertTrue(a.success)

    def test_failure_flag(self):
        a = DeliveryAttempt(1, 0.0, 500, False)
        self.assertFalse(a.success)

    def test_timeout_no_status_code(self):
        a = DeliveryAttempt(1, 0.0, None, False, error="timeout")
        self.assertIsNone(a.status_code)
        self.assertEqual(a.error, "timeout")


class TestDeliveryResult(unittest.TestCase):

    def test_enum_values(self):
        self.assertEqual(DeliveryResult.SUCCESS.value, "success")
        self.assertEqual(DeliveryResult.FAILED.value, "failed")
        self.assertEqual(DeliveryResult.EXHAUSTED.value, "exhausted")
        self.assertEqual(DeliveryResult.DEAD_LETTERED.value, "dead_lettered")

    def test_membership(self):
        results = list(DeliveryResult)
        self.assertIn(DeliveryResult.SUCCESS, results)
        self.assertIn(DeliveryResult.DEAD_LETTERED, results)


class TestDeliveryReport(unittest.TestCase):

    def _make_report(self, result=DeliveryResult.SUCCESS, n_attempts=1):
        attempts = [DeliveryAttempt(i + 1, float(i), 200 if i == n_attempts - 1 else 500, i == n_attempts - 1) for i in range(n_attempts)]
        return DeliveryReport("id-1", result, attempts)

    def test_attempt_count(self):
        r = self._make_report(n_attempts=3)
        self.assertEqual(r.attempt_count, 3)

    def test_successful_attempt_found(self):
        r = self._make_report(result=DeliveryResult.SUCCESS, n_attempts=2)
        self.assertIsNotNone(r.successful_attempt)

    def test_successful_attempt_none_on_failure(self):
        r = DeliveryReport("id", DeliveryResult.EXHAUSTED, [
            DeliveryAttempt(1, 0.0, 500, False),
        ])
        self.assertIsNone(r.successful_attempt)

    def test_dead_letter_reason(self):
        r = DeliveryReport("id", DeliveryResult.DEAD_LETTERED, [], dead_letter_reason="too_many_retries")
        self.assertEqual(r.dead_letter_reason, "too_many_retries")


# ===========================================================================
# 4. WebhookReport aggregate stats
# ===========================================================================

class TestWebhookReport(unittest.TestCase):

    def _make_report(self, result: DeliveryResult) -> DeliveryReport:
        return DeliveryReport(str(uuid.uuid4()), result)

    def test_total(self):
        r = WebhookReport([self._make_report(DeliveryResult.SUCCESS)] * 5)
        self.assertEqual(r.total, 5)

    def test_success_count(self):
        reports = [self._make_report(DeliveryResult.SUCCESS)] * 3 + \
                  [self._make_report(DeliveryResult.FAILED)] * 2
        wr = WebhookReport(reports)
        self.assertEqual(wr.success_count, 3)

    def test_failed_count(self):
        reports = [self._make_report(DeliveryResult.FAILED)] * 2
        wr = WebhookReport(reports)
        self.assertEqual(wr.failed_count, 2)

    def test_exhausted_count(self):
        wr = WebhookReport([self._make_report(DeliveryResult.EXHAUSTED)])
        self.assertEqual(wr.exhausted_count, 1)

    def test_dead_lettered_count(self):
        wr = WebhookReport([self._make_report(DeliveryResult.DEAD_LETTERED)] * 4)
        self.assertEqual(wr.dead_lettered_count, 4)

    def test_success_rate_full(self):
        wr = WebhookReport([self._make_report(DeliveryResult.SUCCESS)] * 10)
        self.assertAlmostEqual(wr.success_rate, 1.0)

    def test_success_rate_partial(self):
        reports = [self._make_report(DeliveryResult.SUCCESS)] * 3 + \
                  [self._make_report(DeliveryResult.FAILED)] * 7
        wr = WebhookReport(reports)
        self.assertAlmostEqual(wr.success_rate, 0.3)

    def test_success_rate_empty(self):
        wr = WebhookReport([])
        self.assertAlmostEqual(wr.success_rate, 0.0)

    def test_total_zero(self):
        wr = WebhookReport([])
        self.assertEqual(wr.total, 0)


# ===========================================================================
# 5. FakeClock
# ===========================================================================

class TestFakeClock(unittest.TestCase):

    def test_initial_time(self):
        c = FakeClock(start=100.0)
        self.assertEqual(c.now(), 100.0)

    def test_advance(self):
        c = FakeClock(start=0.0)
        c.advance(50.0)
        self.assertEqual(c.now(), 50.0)

    def test_set(self):
        c = FakeClock()
        c.set(999.0)
        self.assertEqual(c.now(), 999.0)

    def test_multiple_advances(self):
        c = FakeClock(start=0.0)
        c.advance(10.0)
        c.advance(20.0)
        self.assertEqual(c.now(), 30.0)

    def test_clock_now_real(self):
        c = Clock()
        t1 = c.now()
        t2 = c.now()
        self.assertGreaterEqual(t2, t1)


# ===========================================================================
# 6. SignatureValidator
# ===========================================================================

class TestSignatureValidator(unittest.TestCase):

    def _make_validator(self, tolerance=300.0, clock_time=1000.0):
        clock = FakeClock(start=clock_time)
        v = SignatureValidator("secret", tolerance_seconds=tolerance, clock=clock)
        return v, clock

    def _sign_event(self, event: WebhookEvent, secret="secret") -> str:
        return sign(event.to_bytes(), secret)

    def test_valid_event_accepted(self):
        v, _ = self._make_validator()
        event = make_event(timestamp=1000.0)
        sig = self._sign_event(event)
        ok, reason = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_wrong_signature_rejected(self):
        v, _ = self._make_validator()
        event = make_event(timestamp=1000.0)
        ok, reason = v.validate(event.to_bytes(), "badsig", event.event_id, event.timestamp)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")

    def test_timestamp_too_old_rejected(self):
        v, _ = self._make_validator(tolerance=300.0, clock_time=2000.0)
        event = make_event(timestamp=1000.0)  # 1000s ago
        sig = self._sign_event(event)
        ok, reason = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertFalse(ok)
        self.assertEqual(reason, "timestamp_out_of_tolerance")

    def test_timestamp_just_within_tolerance(self):
        v, _ = self._make_validator(tolerance=300.0, clock_time=1299.0)
        event = make_event(timestamp=1000.0)  # 299s ago
        sig = self._sign_event(event)
        ok, reason = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertTrue(ok)

    def test_timestamp_just_outside_tolerance(self):
        v, _ = self._make_validator(tolerance=300.0, clock_time=1301.0)
        event = make_event(timestamp=1000.0)  # 301s ago
        sig = self._sign_event(event)
        ok, reason = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertFalse(ok)
        self.assertEqual(reason, "timestamp_out_of_tolerance")

    def test_future_timestamp_within_tolerance(self):
        v, _ = self._make_validator(tolerance=300.0, clock_time=1000.0)
        event = make_event(timestamp=1200.0)  # 200s in future
        sig = self._sign_event(event)
        ok, reason = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertTrue(ok)

    def test_replay_rejected(self):
        v, _ = self._make_validator()
        event = make_event(timestamp=1000.0)
        sig = self._sign_event(event)
        ok1, _ = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        ok2, reason2 = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "replay_detected")

    def test_different_ids_not_replays(self):
        v, _ = self._make_validator()
        e1 = make_event(timestamp=1000.0)
        e2 = make_event(timestamp=1000.0)
        sig1 = self._sign_event(e1)
        sig2 = self._sign_event(e2)
        ok1, _ = v.validate(e1.to_bytes(), sig1, e1.event_id, e1.timestamp)
        ok2, _ = v.validate(e2.to_bytes(), sig2, e2.event_id, e2.timestamp)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

    def test_reset_clears_seen_ids(self):
        v, _ = self._make_validator()
        event = make_event(timestamp=1000.0)
        sig = self._sign_event(event)
        v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        v.reset()
        ok, _ = v.validate(event.to_bytes(), sig, event.event_id, event.timestamp)
        self.assertTrue(ok)

    def test_empty_sig_rejected(self):
        v, _ = self._make_validator()
        event = make_event(timestamp=1000.0)
        ok, reason = v.validate(event.to_bytes(), "", event.event_id, event.timestamp)
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid_signature")


# ===========================================================================
# 7. RetryConfig backoff schedule
# ===========================================================================

class TestRetryConfig(unittest.TestCase):

    def test_default_max_attempts(self):
        cfg = RetryConfig()
        self.assertEqual(cfg.max_attempts, 4)

    def test_backoff_attempt_1(self):
        cfg = RetryConfig(base_backoff=2.0)
        self.assertAlmostEqual(cfg.backoff_for(1), 2.0)

    def test_backoff_attempt_2(self):
        cfg = RetryConfig(base_backoff=2.0)
        self.assertAlmostEqual(cfg.backoff_for(2), 4.0)

    def test_backoff_attempt_3(self):
        cfg = RetryConfig(base_backoff=2.0)
        self.assertAlmostEqual(cfg.backoff_for(3), 8.0)

    def test_backoff_attempt_4(self):
        cfg = RetryConfig(base_backoff=2.0)
        self.assertAlmostEqual(cfg.backoff_for(4), 16.0)

    def test_backoff_capped_at_max(self):
        cfg = RetryConfig(base_backoff=2.0, max_backoff=10.0)
        self.assertAlmostEqual(cfg.backoff_for(10), 10.0)

    def test_default_retryable_codes(self):
        cfg = RetryConfig()
        self.assertIn(500, cfg.retryable_status_codes)
        self.assertIn(503, cfg.retryable_status_codes)
        self.assertNotIn(400, cfg.retryable_status_codes)

    def test_custom_retryable_codes(self):
        cfg = RetryConfig(retryable_status_codes=[429, 503])
        self.assertIn(429, cfg.retryable_status_codes)
        self.assertNotIn(500, cfg.retryable_status_codes)


# ===========================================================================
# 8. WebhookSender with MockWebhookHandler (real HTTP)
# ===========================================================================

class TestWebhookSenderSuccess(unittest.TestCase):

    def test_single_success(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=3))
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.SUCCESS)
        self.assertEqual(report.attempt_count, 1)

    def test_no_sleep_on_success(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
        self.assertEqual(sender.sleep_calls, [])

    def test_server_receives_correct_event_id(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            self.assertEqual(srv.received_requests[0]["event_id"], event.event_id)

    def test_server_receives_signature_header(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            sig = srv.received_requests[0]["sig"]
            self.assertEqual(len(sig), 64)


class TestWebhookSenderRetry(unittest.TestCase):

    def test_retry_on_500(self):
        with MockWebhookHandler() as srv:
            srv.set_status_sequence([500, 500, 200])
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=4))
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.SUCCESS)
        self.assertEqual(report.attempt_count, 3)

    def test_backoff_schedule_recorded(self):
        with MockWebhookHandler() as srv:
            srv.set_status_sequence([500, 500, 200])
            cfg = RetryConfig(max_attempts=4, base_backoff=2.0)
            sender = NoSleepSender(srv.url, "secret", cfg)
            event = make_event(timestamp=time.time())
            sender.send(event)
        # Sleeps after attempt 1 (2s) and attempt 2 (4s)
        self.assertEqual(sender.sleep_calls, [2.0, 4.0])

    def test_backoff_schedule_three_failures(self):
        with MockWebhookHandler() as srv:
            srv.set_status_sequence([500, 500, 500, 200])
            cfg = RetryConfig(max_attempts=5, base_backoff=2.0)
            sender = NoSleepSender(srv.url, "secret", cfg)
            event = make_event(timestamp=time.time())
            sender.send(event)
        self.assertEqual(sender.sleep_calls, [2.0, 4.0, 8.0])

    def test_exhausted_after_max_attempts(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            cfg = RetryConfig(max_attempts=3)
            sender = NoSleepSender(srv.url, "secret", cfg)
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.EXHAUSTED)
        self.assertEqual(report.attempt_count, 3)

    def test_no_retry_on_400(self):
        with MockWebhookHandler() as srv:
            srv.set_response(400)
            cfg = RetryConfig(max_attempts=4)
            sender = NoSleepSender(srv.url, "secret", cfg)
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.FAILED)
        self.assertEqual(report.attempt_count, 1)
        self.assertEqual(sender.sleep_calls, [])

    def test_no_retry_on_404(self):
        with MockWebhookHandler() as srv:
            srv.set_response(404)
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=3))
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.FAILED)
        self.assertEqual(report.attempt_count, 1)

    def test_2xx_stops_retries_immediately(self):
        with MockWebhookHandler() as srv:
            srv.set_status_sequence([201])
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=5))
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.SUCCESS)
        self.assertEqual(report.attempt_count, 1)


class TestWebhookSenderDedup(unittest.TestCase):

    def test_duplicate_returns_cached(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            r1 = sender.send(event)
            r2 = sender.send(event)
        self.assertIs(r1, r2)

    def test_duplicate_no_extra_http_request(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            sender.send(event)
            self.assertEqual(srv.request_count(), 1)

    def test_different_ids_both_delivered(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            e1 = make_event(timestamp=time.time())
            e2 = make_event(timestamp=time.time())
            sender.send(e1)
            sender.send(e2)
            self.assertEqual(srv.request_count(), 2)

    def test_reset_dedup_allows_resend(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            sender.reset_dedup()
            sender.send(event)
            self.assertEqual(srv.request_count(), 2)

    def test_exactly_once_side_effect(self):
        """Duplicate event must not trigger a second HTTP request."""
        call_count = [0]
        with MockWebhookHandler() as srv:
            original_handle = srv._handle

            def counting_handle(**kwargs):
                call_count[0] += 1
                return original_handle(**kwargs)

            srv._handle = counting_handle
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            sender.send(event)
            sender.send(event)
        self.assertEqual(call_count[0], 1)


class TestWebhookSenderDLQ(unittest.TestCase):

    def test_exhausted_event_goes_to_dlq(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            dlq = DeadLetterQueue()
            cfg = RetryConfig(max_attempts=2)
            sender = NoSleepSender(srv.url, "secret", cfg, dlq=dlq)
            event = make_event(timestamp=time.time())
            sender.send(event)
        self.assertEqual(dlq.size, 1)
        items = dlq.peek()
        self.assertEqual(items[0][0].event_id, event.event_id)

    def test_non_retryable_failure_goes_to_dlq(self):
        with MockWebhookHandler() as srv:
            srv.set_response(400)
            dlq = DeadLetterQueue()
            sender = NoSleepSender(srv.url, "secret", RetryConfig(), dlq=dlq)
            event = make_event(timestamp=time.time())
            sender.send(event)
        self.assertEqual(dlq.size, 1)

    def test_success_not_in_dlq(self):
        with MockWebhookHandler() as srv:
            dlq = DeadLetterQueue()
            sender = NoSleepSender(srv.url, "secret", dlq=dlq)
            event = make_event(timestamp=time.time())
            sender.send(event)
        self.assertEqual(dlq.size, 0)

    def test_dlq_drain_clears(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            dlq = DeadLetterQueue()
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=1), dlq=dlq)
            event = make_event(timestamp=time.time())
            sender.send(event)
        items = dlq.drain()
        self.assertEqual(len(items), 1)
        self.assertEqual(dlq.size, 0)

    def test_dlq_reason_set(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            dlq = DeadLetterQueue()
            cfg = RetryConfig(max_attempts=1)
            sender = NoSleepSender(srv.url, "secret", cfg, dlq=dlq)
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertIsNotNone(report.dead_letter_reason)


# ===========================================================================
# 9. DeadLetterQueue
# ===========================================================================

class TestDeadLetterQueue(unittest.TestCase):

    def test_enqueue_peek(self):
        dlq = DeadLetterQueue()
        event = make_event()
        dlq.enqueue(event, "reason")
        items = dlq.peek()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0][1], "reason")

    def test_size(self):
        dlq = DeadLetterQueue()
        for _ in range(5):
            dlq.enqueue(make_event(), "r")
        self.assertEqual(dlq.size, 5)

    def test_drain_returns_all(self):
        dlq = DeadLetterQueue()
        for i in range(3):
            dlq.enqueue(make_event(), f"r{i}")
        items = dlq.drain()
        self.assertEqual(len(items), 3)

    def test_drain_empties_queue(self):
        dlq = DeadLetterQueue()
        dlq.enqueue(make_event(), "r")
        dlq.drain()
        self.assertEqual(dlq.size, 0)

    def test_peek_non_destructive(self):
        dlq = DeadLetterQueue()
        dlq.enqueue(make_event(), "r")
        dlq.peek()
        self.assertEqual(dlq.size, 1)

    def test_thread_safety(self):
        dlq = DeadLetterQueue()
        errors = []

        def producer():
            try:
                for _ in range(50):
                    dlq.enqueue(make_event(), "r")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=producer) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(dlq.size, 200)


# ===========================================================================
# 10. SequenceTracker
# ===========================================================================

class TestSequenceTracker(unittest.TestCase):

    def test_in_order_no_gaps(self):
        st = SequenceTracker()
        for i in range(5):
            result = st.record(i)
            self.assertEqual(result, "ok")
        self.assertEqual(st.gaps, [])
        self.assertEqual(st.out_of_order_events, [])

    def test_gap_detected(self):
        st = SequenceTracker()
        st.record(0)
        result = st.record(2)  # skipped 1
        self.assertEqual(result, "gap")
        self.assertEqual(len(st.gaps), 1)

    def test_out_of_order_detected(self):
        st = SequenceTracker()
        st.record(0)
        st.record(1)
        result = st.record(0)  # already past 0
        self.assertEqual(result, "out_of_order")
        self.assertEqual(len(st.out_of_order_events), 1)

    def test_first_event_always_ok(self):
        st = SequenceTracker()
        result = st.record(5)  # any starting number is ok
        self.assertEqual(result, "ok")

    def test_reset_clears_state(self):
        st = SequenceTracker()
        st.record(0)
        st.record(5)  # gap
        st.reset()
        self.assertEqual(st.gaps, [])
        self.assertEqual(st.out_of_order_events, [])
        result = st.record(0)
        self.assertEqual(result, "ok")

    def test_multiple_gaps(self):
        st = SequenceTracker()
        st.record(0)
        st.record(3)  # gap
        st.record(7)  # gap
        self.assertEqual(len(st.gaps), 2)

    def test_gap_stores_expected_and_received(self):
        st = SequenceTracker()
        st.record(0)
        st.record(3)
        gap = st.gaps[0]
        self.assertEqual(gap[0], 1)   # expected
        self.assertEqual(gap[1], 3)   # received

    def test_consecutive_after_gap_ok(self):
        st = SequenceTracker()
        st.record(0)
        st.record(2)  # gap at 1
        result = st.record(3)  # continues after gap
        self.assertEqual(result, "ok")


# ===========================================================================
# 11. MockWebhookHandler
# ===========================================================================

class TestMockWebhookHandler(unittest.TestCase):

    def test_starts_and_stops(self):
        srv = MockWebhookHandler()
        srv.start()
        srv.stop()

    def test_context_manager(self):
        with MockWebhookHandler() as srv:
            self.assertIsNotNone(srv.url)
            self.assertIn("http://", srv.url)

    def test_receives_post(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            self.assertEqual(srv.request_count(), 1)

    def test_set_response_status(self):
        with MockWebhookHandler() as srv:
            srv.set_response(201)
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.SUCCESS)

    def test_clear_requests(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(timestamp=time.time())
            sender.send(event)
            srv.clear_requests()
            self.assertEqual(srv.request_count(), 0)

    def test_status_sequence(self):
        with MockWebhookHandler() as srv:
            srv.set_status_sequence([503, 200])
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=3))
            event = make_event(timestamp=time.time())
            report = sender.send(event)
        self.assertEqual(report.result, DeliveryResult.SUCCESS)
        self.assertEqual(report.attempt_count, 2)

    def test_dynamic_port_assigned(self):
        with MockWebhookHandler(port=0) as srv:
            self.assertGreater(srv.port, 0)

    def test_multiple_events_recorded(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            for _ in range(5):
                event = make_event(timestamp=time.time())
                sender.send(event)
            self.assertEqual(srv.request_count(), 5)

    def test_request_body_decodable(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            event = make_event(payload={"amount": 99}, timestamp=time.time())
            sender.send(event)
            body = srv.received_requests[0]["body"]
            data = json.loads(body)
            self.assertEqual(data["payload"]["amount"], 99)


# ===========================================================================
# 12. End-to-end / integration
# ===========================================================================

class TestEndToEnd(unittest.TestCase):

    def test_full_pipeline_success(self):
        clock = FakeClock(start=5000.0)
        with MockWebhookHandler() as srv:
            validator = SignatureValidator("sharedSecret", tolerance_seconds=300, clock=clock)
            sender = NoSleepSender(srv.url, "sharedSecret")
            event = make_event(timestamp=5000.0)
            report = sender.send(event)

        self.assertEqual(report.result, DeliveryResult.SUCCESS)

        req = srv.received_requests[0]
        payload_bytes = req["body"]
        sig = req["sig"]
        ok, reason = validator.validate(payload_bytes, sig, event.event_id, event.timestamp)
        self.assertTrue(ok, reason)

    def test_webhook_report_aggregation(self):
        with MockWebhookHandler() as srv:
            dlq = DeadLetterQueue()
            cfg_ok = RetryConfig(max_attempts=1)
            sender_ok = NoSleepSender(srv.url, "secret", cfg_ok, dlq=dlq)

            reports_list = []
            for _ in range(4):
                e = make_event(timestamp=time.time())
                reports_list.append(sender_ok.send(e))

            srv.set_response(500)
            cfg_fail = RetryConfig(max_attempts=1)
            sender_fail = NoSleepSender(srv.url, "secret", cfg_fail, dlq=dlq)
            for _ in range(2):
                e = make_event(timestamp=time.time())
                reports_list.append(sender_fail.send(e))

        wr = WebhookReport(reports_list)
        self.assertEqual(wr.total, 6)
        self.assertEqual(wr.success_count, 4)
        self.assertAlmostEqual(wr.success_rate, 4 / 6)

    def test_sequence_tracking_with_delivery(self):
        with MockWebhookHandler() as srv:
            sender = NoSleepSender(srv.url, "secret")
            tracker = SequenceTracker()
            events = [make_event(seq=i, timestamp=time.time()) for i in range(5)]
            for e in events:
                sender.send(e)
                tracker.record(e.sequence_number)
        self.assertEqual(tracker.gaps, [])
        self.assertEqual(tracker.out_of_order_events, [])

    def test_replay_protection_end_to_end(self):
        clock = FakeClock(start=1000.0)
        validator = SignatureValidator("secret", tolerance_seconds=300, clock=clock)
        event = make_event(timestamp=1000.0)
        payload = event.to_bytes()
        sig = sign(payload, "secret")

        ok1, _ = validator.validate(payload, sig, event.event_id, event.timestamp)
        ok2, reason = validator.validate(payload, sig, event.event_id, event.timestamp)
        self.assertTrue(ok1)
        self.assertFalse(ok2)
        self.assertEqual(reason, "replay_detected")

    def test_backoff_doubles_each_attempt(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            cfg = RetryConfig(max_attempts=5, base_backoff=2.0, max_backoff=100.0)
            sender = NoSleepSender(srv.url, "secret", cfg)
            event = make_event(timestamp=time.time())
            sender.send(event)
        expected = [2.0, 4.0, 8.0, 16.0]
        self.assertEqual(sender.sleep_calls, expected)

    def test_dlq_contains_reason_string(self):
        with MockWebhookHandler() as srv:
            srv.set_response(500)
            dlq = DeadLetterQueue()
            sender = NoSleepSender(srv.url, "secret", RetryConfig(max_attempts=1), dlq=dlq)
            event = make_event(timestamp=time.time())
            sender.send(event)
        items = dlq.peek()
        self.assertIsNotNone(items[0][1])
        self.assertIsInstance(items[0][1], str)
        self.assertGreater(len(items[0][1]), 0)


# ===========================================================================
# 13. Teeth — frozen signature/replay corpus catches a planted validator bug
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The campaign teeth contract: prove(oracle) is False, prove(mutant) is
    True for every planted mutant, and the frozen corpus is non-empty and
    non-circular."""

    def test_oracle_is_clean(self):
        # The correct validator must NOT be flagged by its own corpus.
        self.assertFalse(prove(oracle_validate))

    def test_every_mutant_caught(self):
        for mutant in TEETH.mutants:
            with self.subTest(mutant=mutant.name):
                self.assertTrue(prove(mutant.impl),
                                f"mutant {mutant.name} slipped past the corpus")

    def test_skips_replay_check_caught(self):
        self.assertTrue(prove(skips_replay_check))

    def test_off_by_one_tolerance_caught(self):
        self.assertTrue(prove(off_by_one_tolerance))

    def test_corpus_nonempty(self):
        self.assertGreaterEqual(len(SIG_CORPUS), 1)
        self.assertEqual(TEETH.corpus_size, len(SIG_CORPUS))

    def test_teeth_kind_and_mutants(self):
        self.assertEqual(TEETH.kind, "oracle_swap")
        self.assertGreaterEqual(len(TEETH.mutants), 1)
        self.assertIs(TEETH.oracle, oracle_validate)

    def test_oracle_reproduces_every_frozen_verdict(self):
        # Each frozen (expected_ok, expected_reason) must match the real oracle.
        for case in SIG_CORPUS:
            with self.subTest(case=case.name):
                ok, reason = oracle_validate(
                    case.payload, case.sig, case.event_id, case.event_timestamp,
                )
                self.assertEqual(ok, case.expected_ok)
                self.assertEqual(reason, case.expected_reason)

    def test_prove_is_noncircular(self):
        # Flipping ONE frozen literal must make prove(oracle) report caught.
        # This proves prove() compares to baked literals, not to the oracle.
        import dataclasses

        original = SIG_CORPUS[0]
        corrupted = dataclasses.replace(
            original, expected_ok=not original.expected_ok,
        )
        patched = (corrupted,) + tuple(SIG_CORPUS[1:])

        def prove_against(impl, corpus):
            for case in corpus:
                try:
                    ok, reason = impl(
                        case.payload, case.sig, case.event_id, case.event_timestamp,
                    )
                except Exception:
                    return True
                if bool(ok) != case.expected_ok or reason != case.expected_reason:
                    return True
            return False

        # Sanity: against the real corpus the oracle is clean...
        self.assertFalse(prove_against(oracle_validate, SIG_CORPUS))
        # ...but flip one literal and the same oracle is now "caught", which is
        # only possible if prove compares to the frozen literal (non-circular).
        self.assertTrue(prove_against(oracle_validate, patched))

    def test_prove_catches_raising_impl(self):
        def boom(*_args, **_kwargs):
            raise RuntimeError("kaboom")

        self.assertTrue(prove(boom))

    def test_list_scenarios_matches_corpus(self):
        self.assertEqual(list_scenarios(), [c.name for c in SIG_CORPUS])

    def test_self_test_passes(self):
        from harnesses.core.webhook_test_harness import _run_self_test
        self.assertEqual(_run_self_test(as_json=False), 0)


if __name__ == "__main__":
    unittest.main()
