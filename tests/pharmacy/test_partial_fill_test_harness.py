import json
import threading
import time
import unittest
import urllib.error
import urllib.request

from harnesses.pharmacy.partial_fill_test_harness import (
    AuditCapture,
    BuggyPartialFillStore,
    BuggyPartialFillStore2,
    PartialFillStore,
    run_all_scenarios,
    start_mock_server,
)


class TestPartialFillStore(unittest.TestCase):
    def setUp(self):
        self.s = PartialFillStore()

    def _add(self, drug="Drug", qty=10, patient="P", date="2026-05-25"):
        return self.s.add(drug, qty, patient, date)

    def test_add_returns_positive_int(self):
        pid = self._add()
        self.assertIsInstance(pid, int)
        self.assertGreater(pid, 0)

    def test_open_partial_correct_fields(self):
        pid = self._add("Amoxicillin", 30, "Alice", "2026-05-25")
        rows = self.s.list_open()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["drug"], "Amoxicillin")
        self.assertEqual(r["qty_owed"], 30)
        self.assertEqual(r["patient"], "Alice")
        self.assertEqual(r["resolved"], 0)

    def test_resolved_disappears_from_list_open(self):
        pid = self._add()
        self.s.resolve(pid)
        self.assertEqual(len(self.s.list_open()), 0)

    def test_resolve_true_first_call(self):
        pid = self._add()
        self.assertTrue(self.s.resolve(pid))

    def test_resolve_false_second_call(self):
        pid = self._add()
        self.s.resolve(pid)
        self.assertFalse(self.s.resolve(pid))

    def test_resolve_nonexistent_false(self):
        self.assertFalse(self.s.resolve(99999))

    def test_count_open_five(self):
        for i in range(5):
            self._add(f"Drug{i}")
        self.assertEqual(self.s.count_open(), 5)

    def test_count_open_excludes_resolved(self):
        pids = [self._add(f"Drug{i}") for i in range(5)]
        self.s.resolve(pids[0])
        self.s.resolve(pids[1])
        self.assertEqual(self.s.count_open(), 3)

    def test_list_open_newest_first(self):
        p1 = self._add("A")
        p2 = self._add("B")
        p3 = self._add("C")
        ids = [r["id"] for r in self.s.list_open()]
        self.assertEqual(ids, [p3, p2, p1])

    def test_resolve_middle_leaves_others_open(self):
        p1 = self._add("Drug1")
        p2 = self._add("Drug2")
        p3 = self._add("Drug3")
        self.s.resolve(p2)
        open_ids = {r["id"] for r in self.s.list_open()}
        self.assertEqual(open_ids, {p1, p3})

    def test_qty_owed_preserved(self):
        self._add(qty=99)
        row = self.s.list_open()[0]
        self.assertEqual(row["qty_owed"], 99)

    def test_independent_store_instances(self):
        s2 = PartialFillStore()
        p1 = self.s.add("Drug1", 1, "P1", "2026-05-25")
        s2.add("Drug2", 1, "P2", "2026-05-25")
        self.s.resolve(p1)
        self.assertEqual(s2.count_open(), 1)


class TestConcurrentResolve(unittest.TestCase):
    def test_concurrent_resolve_exactly_one_true(self):
        s = PartialFillStore()
        pid = s.add("RaceDrug", 1, "RaceP", "2026-05-25")
        outcomes = []
        barrier = threading.Barrier(2)

        def race():
            barrier.wait()
            outcomes.append(s.resolve(pid))

        threads = [threading.Thread(target=race) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for r in outcomes if r is True), 1)
        self.assertEqual(sum(1 for r in outcomes if r is False), 1)

    def test_concurrent_adds_all_unique_ids(self):
        s = PartialFillStore()
        ids = []
        lock = threading.Lock()

        def add_one(i):
            pid = s.add(f"Drug{i}", i, f"P{i}", "2026-05-25")
            with lock:
                ids.append(pid)

        threads = [threading.Thread(target=add_one, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(set(ids)), 10)
        self.assertEqual(s.count_open(), 10)


class TestAuditCapture(unittest.TestCase):
    def test_logs_only_on_true(self):
        s = PartialFillStore()
        audit = AuditCapture()
        pid = s.add("Drug", 1, "P", "2026-05-25")
        if s.resolve(pid):
            audit.log("resolved")
        if s.resolve(pid):  # False — must not log
            audit.log("resolved")
        self.assertEqual(audit.count(), 1)

    def test_audit_thread_safe(self):
        audit = AuditCapture()
        threads = [
            threading.Thread(target=audit.log, args=(f"msg{i}",)) for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(audit.count(), 20)


class TestBuggyStores(unittest.TestCase):
    def test_buggy_store_always_true_on_second_call(self):
        b = BuggyPartialFillStore()
        pid = b.add("Drug", 1, "P", "2026-05-25")
        b.resolve(pid)
        # Harness proves this bug exists in BuggyStore
        self.assertTrue(b.resolve(pid))

    def test_buggy_store2_leaks_resolved_rows(self):
        b = BuggyPartialFillStore2()
        pid = b.add("Drug", 1, "P", "2026-05-25")
        b.resolve(pid)
        # Harness proves resolved rows show up in list_open on BuggyStore2
        self.assertGreater(len(b.list_open()), 0)


class TestMockServer(unittest.TestCase):
    PORT = 19291  # offset from default to avoid collision

    @classmethod
    def setUpClass(cls):
        cls.srv = start_mock_server(cls.PORT)
        time.sleep(0.15)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def _post(self, path, data=None):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        body = json.dumps(data or {}).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def _get(self, path):
        url = f"http://127.0.0.1:{self.PORT}{path}"
        with urllib.request.urlopen(url) as r:
            return r.status, json.loads(r.read())

    def test_post_partial_returns_201_with_id(self):
        code, data = self._post(
            "/partials",
            {"drug": "Lisinopril", "qty_owed": 5, "patient": "Bob", "date": "2026-05-25"},
        )
        self.assertEqual(code, 201)
        self.assertIn("id", data)
        self.assertIsInstance(data["id"], int)

    def test_get_partials_returns_open_and_count(self):
        code, data = self._get("/partials")
        self.assertEqual(code, 200)
        self.assertIn("open", data)
        self.assertIn("count", data)

    def test_resolve_200_then_409(self):
        _, post_data = self._post(
            "/partials",
            {"drug": "Metformin", "qty_owed": 10, "patient": "Carol", "date": "2026-05-25"},
        )
        pid = post_data["id"]
        code1, d1 = self._post(f"/partials/{pid}/resolve")
        self.assertEqual(code1, 200)
        self.assertTrue(d1["resolved"])
        try:
            code2, d2 = self._post(f"/partials/{pid}/resolve")
            self.assertEqual(code2, 409)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 409)


class TestSelfTest(unittest.TestCase):
    def test_self_test_all_pass(self):
        self.assertTrue(run_all_scenarios(verbose=False))

    def test_self_test_returns_bool(self):
        self.assertIsInstance(run_all_scenarios(verbose=False), bool)


if __name__ == "__main__":
    unittest.main()
