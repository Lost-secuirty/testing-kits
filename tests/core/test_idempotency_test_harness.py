"""
Tests for the Idempotency / Retry-Safety Test Harness (Harness 21 of 36)

~66 tests covering:
- IdempotencyStore (atomic check-and-set, thread-safety, TTL)
- StateOnlyStore (buggy store failure mode)
- KeyDedupTester (deduplication logic)
- RetryConvergenceTester (replay convergence)
- ConcurrentDedupTester (exactly-once under concurrency)
- InProgressTester (409 on PENDING)
- TTLTester (expiry and cleanup)
- ResponsePersistenceTester (correct vs buggy store)
- SafeMethodTester (HTTP method classification)
- MockIdempotencyServer (full HTTP integration tests)
"""

import threading
import time
import unittest
import uuid

from harnesses.core.idempotency_test_harness import (
    ConcurrentDedupTester,
    IdempotencyEntry,
    IdempotencyState,
    IdempotencyStore,
    InProgressTester,
    KeyDedupTester,
    MockIdempotencyServer,
    ResponsePersistenceTester,
    RetryConvergenceTester,
    SafeMethodTester,
    StateOnlyStore,
    TTLTester,
    generate_idempotency_key,
    http_get,
    http_post,
)


def _key() -> str:
    """Generate a unique key for each test."""
    return str(uuid.uuid4())


# ===========================================================================
# 1. IdempotencyStore Tests
# ===========================================================================

class TestIdempotencyStoreBasics(unittest.TestCase):

    def setUp(self):
        self.store = IdempotencyStore()

    # 1. start() on new key returns True
    def test_start_new_key_returns_true(self):
        self.assertTrue(self.store.start(_key()))

    # 2. start() on existing (non-expired) key returns False
    def test_start_duplicate_key_returns_false(self):
        k = _key()
        self.store.start(k)
        self.assertFalse(self.store.start(k))

    # 3. get() returns None for unknown key
    def test_get_unknown_key_returns_none(self):
        self.assertIsNone(self.store.get(_key()))

    # 4. get() returns PENDING entry after start()
    def test_get_after_start_is_pending(self):
        k = _key()
        self.store.start(k)
        entry = self.store.get(k)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.state, IdempotencyState.PENDING)

    # 5. complete() sets state to COMPLETED and saves response
    def test_complete_sets_completed_state(self):
        k = _key()
        self.store.start(k)
        self.store.complete(k, {"result": "ok"})
        entry = self.store.get(k)
        self.assertEqual(entry.state, IdempotencyState.COMPLETED)
        self.assertEqual(entry.response, {"result": "ok"})

    # 6. fail() sets state to FAILED and saves error
    def test_fail_sets_failed_state(self):
        k = _key()
        self.store.start(k)
        self.store.fail(k, "timeout error")
        entry = self.store.get(k)
        self.assertEqual(entry.state, IdempotencyState.FAILED)
        self.assertEqual(entry.error, "timeout error")

    # 7. complete() raises KeyError for non-existent key
    def test_complete_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            self.store.complete(_key(), {"result": "ok"})

    # 8. fail() raises KeyError for non-existent key
    def test_fail_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            self.store.fail(_key(), "error")

    # 9. size() returns correct count
    def test_size_tracks_entries(self):
        for _ in range(3):
            self.store.start(_key())
        self.assertEqual(self.store.size(), 3)

    # 10. clear() removes all entries
    def test_clear_empties_store(self):
        for _ in range(5):
            self.store.start(_key())
        self.store.clear()
        self.assertEqual(self.store.size(), 0)

    # 11. response is preserved across complete() and get()
    def test_response_round_trip(self):
        k = _key()
        payload = {"user_id": 42, "amount": 100.50, "currency": "USD"}
        self.store.start(k)
        self.store.complete(k, payload)
        entry = self.store.get(k)
        self.assertEqual(entry.response, payload)

    # 12. Multiple distinct keys coexist independently
    def test_multiple_keys_independent(self):
        k1, k2 = _key(), _key()
        self.store.start(k1)
        self.store.start(k2)
        self.store.complete(k1, "resp1")
        entry1 = self.store.get(k1)
        entry2 = self.store.get(k2)
        self.assertEqual(entry1.state, IdempotencyState.COMPLETED)
        self.assertEqual(entry2.state, IdempotencyState.PENDING)


class TestIdempotencyStoreTTL(unittest.TestCase):

    def setUp(self):
        self.store = IdempotencyStore()

    # 13. Entry with TTL is accessible before expiry
    def test_entry_accessible_before_ttl(self):
        k = _key()
        self.store.start(k, ttl=10.0)
        self.assertIsNotNone(self.store.get(k))

    # 14. is_expired() returns False for non-expired entry
    def test_is_expired_false_before_ttl(self):
        k = _key()
        self.store.start(k, ttl=10.0)
        self.assertFalse(self.store.is_expired(k))

    # 15. Entry expires after TTL
    def test_entry_expires_after_ttl(self):
        k = _key()
        self.store.start(k, ttl=0.05)
        time.sleep(0.1)
        self.assertIsNone(self.store.get(k))

    # 16. is_expired() returns True after TTL
    def test_is_expired_true_after_ttl(self):
        k = _key()
        self.store.start(k, ttl=0.05)
        time.sleep(0.1)
        self.assertTrue(self.store.is_expired(k))

    # 17. cleanup_expired() removes expired entries
    def test_cleanup_expired_removes_entries(self):
        k1, k2 = _key(), _key()
        self.store.start(k1, ttl=0.05)
        self.store.start(k2, ttl=10.0)
        time.sleep(0.1)
        removed = self.store.cleanup_expired()
        self.assertEqual(removed, 1)
        self.assertEqual(self.store.size(), 1)

    # 18. cleanup_expired() returns 0 when nothing expired
    def test_cleanup_no_expired(self):
        self.store.start(_key(), ttl=10.0)
        removed = self.store.cleanup_expired()
        self.assertEqual(removed, 0)

    # 19. start() can reclaim an expired key
    def test_start_reclaims_expired_key(self):
        k = _key()
        self.store.start(k, ttl=0.05)
        time.sleep(0.1)
        # Should succeed because the entry is expired
        self.assertTrue(self.store.start(k))

    # 20. is_expired() returns False for unknown key
    def test_is_expired_unknown_key_returns_false(self):
        self.assertFalse(self.store.is_expired(_key()))


class TestIdempotencyStoreThreadSafety(unittest.TestCase):

    # 21. Concurrent start() — only one thread wins for the same key
    def test_concurrent_start_exactly_one_wins(self):
        store = IdempotencyStore()
        k = _key()
        results = []
        lock = threading.Lock()
        barrier = threading.Barrier(20)

        def attempt():
            barrier.wait()
            won = store.start(k)
            with lock:
                results.append(won)

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 19)

    # 22. Thread-safe complete() and get() under concurrent access
    def test_concurrent_complete_and_get(self):
        store = IdempotencyStore()
        k = _key()
        store.start(k)
        errors = []

        def completer():
            try:
                store.complete(k, {"result": "done"})
            except Exception as e:
                errors.append(e)

        def reader():
            try:
                store.get(k)
            except Exception as e:
                errors.append(e)

        threads = (
            [threading.Thread(target=completer)] +
            [threading.Thread(target=reader) for _ in range(10)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])


# ===========================================================================
# 2. StateOnlyStore Tests
# ===========================================================================

class TestStateOnlyStore(unittest.TestCase):

    def setUp(self):
        self.store = StateOnlyStore()

    # 23. start() on new key returns True
    def test_start_new_key(self):
        self.assertTrue(self.store.start(_key()))

    # 24. start() on existing key returns False
    def test_start_duplicate_returns_false(self):
        k = _key()
        self.store.start(k)
        self.assertFalse(self.store.start(k))

    # 25. get_state() returns PENDING after start()
    def test_get_state_pending_after_start(self):
        k = _key()
        self.store.start(k)
        self.assertEqual(self.store.get_state(k), IdempotencyState.PENDING)

    # 26. get_state() returns COMPLETED after complete()
    def test_get_state_completed(self):
        k = _key()
        self.store.start(k)
        self.store.complete(k, {"data": "value"})
        self.assertEqual(self.store.get_state(k), IdempotencyState.COMPLETED)

    # 27. get_response() returns None (the bug) even after complete()
    def test_get_response_returns_none_bug(self):
        k = _key()
        self.store.start(k)
        self.store.complete(k, {"important": "data"})
        # Bug: response was not saved
        self.assertIsNone(self.store.get_response(k))

    # 28. get_state() returns None for unknown key
    def test_get_state_unknown_key(self):
        self.assertIsNone(self.store.get_state(_key()))


# ===========================================================================
# 3. KeyDedupTester Tests
# ===========================================================================

class TestKeyDedupTester(unittest.TestCase):

    def setUp(self):
        self.tester = KeyDedupTester()

    def tearDown(self):
        self.tester.reset()

    # 29. First request executes the side effect
    def test_first_request_executes_side_effect(self):
        k = _key()
        result = self.tester.process_request(k, {"amount": 50})
        self.assertFalse(result["cached"])
        self.assertEqual(self.tester.side_effect_count, 1)

    # 30. Second request with same key returns cached (no re-execution)
    def test_second_request_cached(self):
        k = _key()
        r1 = self.tester.process_request(k, {"amount": 50})
        r2 = self.tester.process_request(k, {"amount": 50})
        self.assertFalse(r1["cached"])
        self.assertTrue(r2["cached"])
        self.assertEqual(self.tester.side_effect_count, 1)

    # 31. Cached response equals original response
    def test_cached_response_equals_original(self):
        k = _key()
        r1 = self.tester.process_request(k, {"amount": 50})
        r2 = self.tester.process_request(k, {"amount": 50})
        self.assertEqual(r1["response"], r2["response"])

    # 32. Different keys each execute the side effect
    def test_different_keys_each_execute(self):
        self.tester.process_request(_key(), {"amount": 10})
        self.tester.process_request(_key(), {"amount": 20})
        self.tester.process_request(_key(), {"amount": 30})
        self.assertEqual(self.tester.side_effect_count, 3)

    # 33. reset() clears side-effect count and store
    def test_reset_clears_state(self):
        k = _key()
        self.tester.process_request(k, {})
        self.tester.reset()
        self.assertEqual(self.tester.side_effect_count, 0)
        # After reset, same key should execute again
        self.tester.process_request(k, {})
        self.assertEqual(self.tester.side_effect_count, 1)


# ===========================================================================
# 4. RetryConvergenceTester Tests
# ===========================================================================

class TestRetryConvergenceTester(unittest.TestCase):

    def setUp(self):
        self.tester = RetryConvergenceTester()

    # 34. First call executes; subsequent calls return same response
    def test_execute_once_semantics(self):
        k = _key()
        r1 = self.tester.execute_once(k, "payload")
        r2 = self.tester.execute_once(k, "payload")
        self.assertEqual(r1, r2)
        self.assertEqual(self.tester.execution_count, 1)

    # 35. replay_n_times returns N identical responses
    def test_replay_n_times_all_identical(self):
        k = _key()
        responses = self.tester.replay_n_times(k, {"data": 123}, 10)
        self.assertEqual(len(responses), 10)
        self.assertTrue(self.tester.all_responses_identical(responses))

    # 36. execution_count is exactly 1 after N replays
    def test_execution_count_exactly_one(self):
        k = _key()
        self.tester.replay_n_times(k, "test", 20)
        self.assertEqual(self.tester.execution_count, 1)

    # 37. all_responses_identical returns True for identical list
    def test_all_responses_identical_true(self):
        resp = {"id": "abc", "value": 42}
        self.assertTrue(self.tester.all_responses_identical([resp, resp, resp]))

    # 38. all_responses_identical returns False for different responses
    def test_all_responses_identical_false(self):
        responses = [{"id": "a"}, {"id": "b"}]
        self.assertFalse(self.tester.all_responses_identical(responses))

    # 39. all_responses_identical returns True for empty list
    def test_all_responses_identical_empty(self):
        self.assertTrue(self.tester.all_responses_identical([]))

    # 40. Different keys each produce unique responses
    def test_different_keys_produce_different_responses(self):
        k1, k2 = _key(), _key()
        r1 = self.tester.execute_once(k1, "a")
        r2 = self.tester.execute_once(k2, "b")
        self.assertNotEqual(r1, r2)
        self.assertEqual(self.tester.execution_count, 2)


# ===========================================================================
# 5. ConcurrentDedupTester Tests
# ===========================================================================

class TestConcurrentDedupTester(unittest.TestCase):

    def setUp(self):
        self.tester = ConcurrentDedupTester()

    def tearDown(self):
        self.tester.reset()

    # 41. Exactly one thread executes the side effect
    def test_exactly_one_execution(self):
        k = _key()
        self.tester.run_concurrent(k, {"data": "x"}, n_threads=10)
        self.assertEqual(self.tester.execution_count, 1)

    # 42. All threads get a result (none left as None)
    def test_all_threads_get_result(self):
        k = _key()
        results = self.tester.run_concurrent(k, {"data": "y"}, n_threads=8)
        self.assertEqual(len(results), 8)
        self.assertTrue(all(r is not None for r in results))

    # 43. Exactly one "executed" result among all threads
    def test_exactly_one_executed_tag(self):
        k = _key()
        results = self.tester.run_concurrent(k, {"data": "z"}, n_threads=5)
        executed = [r for r in results if r[0] == "executed"]
        self.assertEqual(len(executed), 1)

    # 44. All cached responses equal the executed response
    def test_cached_responses_match_executed(self):
        k = _key()
        results = self.tester.run_concurrent(k, {"val": 99}, n_threads=6)
        executed = [r[1] for r in results if r[0] == "executed"]
        cached = [r[1] for r in results if r[0] == "cached"]
        if executed and cached:
            for c in cached:
                self.assertEqual(c, executed[0])


# ===========================================================================
# 6. InProgressTester Tests
# ===========================================================================

class TestInProgressTester(unittest.TestCase):

    def setUp(self):
        self.tester = InProgressTester()

    def _noop(self, payload):
        return {"processed": payload}

    # 45. New request returns 201 with result
    def test_new_request_returns_201(self):
        k = _key()
        result = self.tester.handle_request(k, {"x": 1}, self._noop)
        self.assertEqual(result["status_code"], 201)
        self.assertFalse(result["cached"])

    # 46. PENDING key returns 409
    def test_pending_key_returns_409(self):
        k = _key()
        self.tester.store.start(k)  # Manually put in PENDING
        result = self.tester.handle_request(k, {}, self._noop)
        self.assertEqual(result["status_code"], 409)

    # 47. COMPLETED key returns 200 with cached response
    def test_completed_key_returns_200_cached(self):
        k = _key()
        self.tester.store.start(k)
        self.tester.store.complete(k, {"answer": 42})
        result = self.tester.handle_request(k, {}, self._noop)
        self.assertEqual(result["status_code"], 200)
        self.assertTrue(result["cached"])
        self.assertEqual(result["response"]["answer"], 42)

    # 48. FAILED key returns 422 with error
    def test_failed_key_returns_422(self):
        k = _key()
        self.tester.store.start(k)
        self.tester.store.fail(k, "downstream error")
        result = self.tester.handle_request(k, {}, self._noop)
        self.assertEqual(result["status_code"], 422)

    # 49. Second identical call is idempotent (returns cached 200)
    def test_second_call_is_cached(self):
        k = _key()
        r1 = self.tester.handle_request(k, {"val": 5}, self._noop)
        r2 = self.tester.handle_request(k, {"val": 5}, self._noop)
        self.assertEqual(r1["status_code"], 201)
        self.assertEqual(r2["status_code"], 200)
        self.assertTrue(r2["cached"])


# ===========================================================================
# 7. TTLTester Tests
# ===========================================================================

class TestTTLTester(unittest.TestCase):

    def setUp(self):
        self.tester = TTLTester()

    # 50. Entry is accessible before TTL expires
    def test_accessible_before_ttl(self):
        k = _key()
        self.tester.add_entry_with_ttl(k, {"data": "ok"}, ttl=10.0)
        self.assertTrue(self.tester.is_accessible(k))

    # 51. Entry is inaccessible after TTL expires
    def test_inaccessible_after_ttl(self):
        k = _key()
        self.tester.add_entry_with_ttl(k, {"data": "ok"}, ttl=0.05)
        self.tester.wait_for_expiry(k)
        self.assertFalse(self.tester.is_accessible(k))

    # 52. cleanup removes expired entries
    def test_cleanup_removes_expired(self):
        k = _key()
        self.tester.add_entry_with_ttl(k, "resp", ttl=0.05)
        self.tester.wait_for_expiry(k)
        removed = self.tester.cleanup_and_count()
        self.assertGreaterEqual(removed, 1)

    # 53. cleanup returns 0 when nothing expired
    def test_cleanup_no_expired(self):
        self.tester.add_entry_with_ttl(_key(), "resp", ttl=10.0)
        removed = self.tester.cleanup_and_count()
        self.assertEqual(removed, 0)


# ===========================================================================
# 8. ResponsePersistenceTester Tests
# ===========================================================================

class TestResponsePersistenceTester(unittest.TestCase):

    def setUp(self):
        self.tester = ResponsePersistenceTester()

    # 54. Correct store returns original response
    def test_correct_store_returns_response(self):
        result = self.tester.test_correct_store(_key(), {"amount": 100})
        self.assertEqual(result, {"amount": 100})

    # 55. Buggy store returns None (failure mode)
    def test_buggy_store_returns_none(self):
        result = self.tester.test_buggy_store(_key(), {"amount": 100})
        self.assertIsNone(result)

    # 56. demonstrate_failure shows correct store works
    def test_demonstrate_failure_correct_store(self):
        demo = self.tester.demonstrate_failure(_key(), {"x": 1})
        self.assertTrue(demo["correct_store_works"])

    # 57. demonstrate_failure shows buggy store fails
    def test_demonstrate_failure_buggy_store(self):
        demo = self.tester.demonstrate_failure(_key(), {"x": 1})
        self.assertTrue(demo["buggy_store_fails"])

    # 58. correct_store_result matches original value
    def test_correct_store_result_value(self):
        payload = {"transaction_id": "txn_123", "amount": 50.0}
        demo = self.tester.demonstrate_failure(_key(), payload)
        self.assertEqual(demo["correct_store_result"], payload)


# ===========================================================================
# 9. SafeMethodTester Tests
# ===========================================================================

class TestSafeMethodTester(unittest.TestCase):

    def setUp(self):
        self.tester = SafeMethodTester()

    # 59. GET is idempotent
    def test_get_is_idempotent(self):
        self.assertTrue(self.tester.is_idempotent("GET"))

    # 60. POST is NOT idempotent
    def test_post_is_not_idempotent(self):
        self.assertFalse(self.tester.is_idempotent("POST"))

    # 61. PATCH is NOT idempotent
    def test_patch_is_not_idempotent(self):
        self.assertFalse(self.tester.is_idempotent("PATCH"))

    # 62. PUT is idempotent
    def test_put_is_idempotent(self):
        self.assertTrue(self.tester.is_idempotent("PUT"))

    # 63. DELETE is idempotent
    def test_delete_is_idempotent(self):
        self.assertTrue(self.tester.is_idempotent("DELETE"))

    # 64. HEAD is idempotent and safe
    def test_head_is_idempotent_and_safe(self):
        self.assertTrue(self.tester.is_idempotent("HEAD"))
        self.assertTrue(self.tester.is_safe("HEAD"))

    # 65. OPTIONS is idempotent and safe
    def test_options_is_idempotent_and_safe(self):
        self.assertTrue(self.tester.is_idempotent("OPTIONS"))
        self.assertTrue(self.tester.is_safe("OPTIONS"))

    # 66. classify() returns correct fields for POST
    def test_classify_post(self):
        c = self.tester.classify("POST")
        self.assertEqual(c["method"], "POST")
        self.assertFalse(c["idempotent"])
        self.assertFalse(c["safe"])
        self.assertTrue(c["requires_idempotency_key"])

    # 67. classify() returns correct fields for GET
    def test_classify_get(self):
        c = self.tester.classify("GET")
        self.assertTrue(c["idempotent"])
        self.assertTrue(c["safe"])
        self.assertFalse(c["requires_idempotency_key"])

    # 68. methods_requiring_key returns POST and PATCH
    def test_methods_requiring_key(self):
        methods = set(self.tester.methods_requiring_key())
        self.assertIn("POST", methods)
        self.assertIn("PATCH", methods)

    # 69. idempotent_methods returns expected set
    def test_idempotent_methods_list(self):
        methods = set(self.tester.idempotent_methods())
        self.assertIn("GET", methods)
        self.assertIn("PUT", methods)
        self.assertIn("DELETE", methods)
        self.assertIn("HEAD", methods)
        self.assertIn("OPTIONS", methods)

    # 70. case-insensitive is_idempotent
    def test_is_idempotent_case_insensitive(self):
        self.assertTrue(self.tester.is_idempotent("get"))
        self.assertTrue(self.tester.is_idempotent("Put"))
        self.assertFalse(self.tester.is_idempotent("post"))


# ===========================================================================
# 10. MockIdempotencyServer Integration Tests
# ===========================================================================

class TestMockIdempotencyServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.server = MockIdempotencyServer(port=0)
        cls.server.start()
        cls.base_url = cls.server.base_url

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    # 71. POST without idempotency key returns 400
    def test_post_without_key_returns_400(self):
        result = http_post(f"{self.base_url}/orders", {"item": "widget"})
        self.assertEqual(result["status_code"], 400)
        self.assertIn("MISSING_IDEMPOTENCY_KEY", result["body"]["code"])

    # 72. POST with idempotency key returns 201 on first call
    def test_post_with_key_first_call_201(self):
        k = _key()
        result = http_post(
            f"{self.base_url}/orders",
            {"item": "widget"},
            headers={"X-Idempotency-Key": k}
        )
        self.assertEqual(result["status_code"], 201)
        self.assertFalse(result["body"]["cached"])

    # 73. POST with same key second call returns 200 (cached)
    def test_post_same_key_second_call_cached(self):
        k = _key()
        http_post(
            f"{self.base_url}/orders",
            {"item": "widget"},
            headers={"X-Idempotency-Key": k}
        )
        result = http_post(
            f"{self.base_url}/orders",
            {"item": "widget"},
            headers={"X-Idempotency-Key": k}
        )
        self.assertEqual(result["status_code"], 200)
        self.assertTrue(result["body"]["cached"])

    # 74. GET endpoint always returns 200 without idempotency key
    def test_get_endpoint_no_key_required(self):
        result = http_get(f"{self.base_url}/items")
        self.assertEqual(result["status_code"], 200)

    # 75. Two different keys produce independent responses
    def test_different_keys_independent_responses(self):
        k1, k2 = _key(), _key()
        r1 = http_post(
            f"{self.base_url}/orders",
            {"item": "a"},
            headers={"X-Idempotency-Key": k1}
        )
        r2 = http_post(
            f"{self.base_url}/orders",
            {"item": "b"},
            headers={"X-Idempotency-Key": k2}
        )
        self.assertEqual(r1["status_code"], 201)
        self.assertEqual(r2["status_code"], 201)
        # Both created independently
        id1 = r1["body"]["response"]["id"]
        id2 = r2["body"]["response"]["id"]
        self.assertNotEqual(id1, id2)

    # 76. Cached response body equals original response body
    def test_cached_response_body_matches_original(self):
        k = _key()
        r1 = http_post(
            f"{self.base_url}/payments",
            {"amount": 200},
            headers={"X-Idempotency-Key": k}
        )
        r2 = http_post(
            f"{self.base_url}/payments",
            {"amount": 200},
            headers={"X-Idempotency-Key": k}
        )
        self.assertEqual(r1["body"]["response"], r2["body"]["response"])

    # 77. Server context manager starts and stops cleanly
    def test_context_manager_lifecycle(self):
        with MockIdempotencyServer(port=0) as srv:
            self.assertIsNotNone(srv.port)
            self.assertGreater(srv.port, 0)
            result = http_get(f"{srv.base_url}/health")
            self.assertEqual(result["status_code"], 200)

    # 78. generate_idempotency_key produces unique UUIDs
    def test_generate_key_is_unique(self):
        keys = {generate_idempotency_key() for _ in range(100)}
        self.assertEqual(len(keys), 100)


# ===========================================================================
# 11. IdempotencyEntry Tests
# ===========================================================================

class TestIdempotencyEntry(unittest.TestCase):

    # 79. Entry defaults to not expired when no TTL
    def test_entry_no_ttl_never_expires(self):
        entry = IdempotencyEntry(key="k", state=IdempotencyState.PENDING)
        self.assertFalse(entry.is_expired())

    # 80. Entry with future TTL is not expired
    def test_entry_future_ttl_not_expired(self):
        entry = IdempotencyEntry(key="k", state=IdempotencyState.PENDING, ttl=100.0)
        self.assertFalse(entry.is_expired())

    # 81. Entry with past TTL is expired
    def test_entry_past_ttl_is_expired(self):
        entry = IdempotencyEntry(
            key="k",
            state=IdempotencyState.PENDING,
            created_at=time.time() - 10.0,
            ttl=5.0
        )
        self.assertTrue(entry.is_expired())

    # 82. IdempotencyState enum has expected values
    def test_state_enum_values(self):
        self.assertEqual(IdempotencyState.PENDING.value, "PENDING")
        self.assertEqual(IdempotencyState.COMPLETED.value, "COMPLETED")
        self.assertEqual(IdempotencyState.FAILED.value, "FAILED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
