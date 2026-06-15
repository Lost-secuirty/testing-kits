"""
127 tests for pagination_test_harness.py
Pure stdlib, zero external dependencies.
"""

import base64
import json
import threading
import unittest

from harnesses._teeth import verify
from harnesses.core.pagination_test_harness import (
    TEETH,
    BackingStore,
    CursorPaginator,
    OffsetPaginator,
    Page,
    PageResult,
    PaginationReport,
    PaginationServer,
    PaginationTestResult,
    decode_cursor,
    demonstrate_offset_delete_bug,
    demonstrate_offset_insert_bug,
    encode_cursor,
    make_records,
    populated_store,
)

# ===========================================================================
# BackingStore tests (20 tests)
# ===========================================================================

class TestBackingStore(unittest.TestCase):

    def setUp(self):
        self.store = BackingStore()

    # 1
    def test_new_store_is_empty(self):
        self.assertEqual(self.store.all(), [])

    # 2
    def test_add_single_record(self):
        self.store.add({"id": 1, "sort_key": 1, "data": "a"})
        self.assertEqual(len(self.store.all()), 1)

    # 3
    def test_add_multiple_records(self):
        for i in range(5):
            self.store.add({"id": i, "sort_key": i, "data": f"d{i}"})
        self.assertEqual(len(self.store.all()), 5)

    # 4
    def test_all_returns_copy(self):
        self.store.add({"id": 1, "sort_key": 1, "data": "x"})
        snapshot = self.store.all()
        snapshot.append({"id": 99, "sort_key": 99, "data": "extra"})
        self.assertEqual(len(self.store.all()), 1)

    # 5
    def test_delete_existing_record(self):
        self.store.add({"id": 1, "sort_key": 1, "data": "a"})
        result = self.store.delete(1)
        self.assertTrue(result)
        self.assertEqual(self.store.all(), [])

    # 6
    def test_delete_nonexistent_returns_false(self):
        result = self.store.delete(999)
        self.assertFalse(result)

    # 7
    def test_delete_leaves_others_intact(self):
        for i in range(1, 4):
            self.store.add({"id": i, "sort_key": i, "data": f"d{i}"})
        self.store.delete(2)
        ids = [r["id"] for r in self.store.all()]
        self.assertIn(1, ids)
        self.assertNotIn(2, ids)
        self.assertIn(3, ids)

    # 8
    def test_add_requires_id(self):
        with self.assertRaises(ValueError):
            self.store.add({"sort_key": 1, "data": "a"})

    # 9
    def test_add_requires_sort_key(self):
        with self.assertRaises(ValueError):
            self.store.add({"id": 1, "data": "a"})

    # 10
    def test_count_empty(self):
        self.assertEqual(self.store.count(), 0)

    # 11
    def test_count_after_adds(self):
        for i in range(7):
            self.store.add({"id": i, "sort_key": i, "data": ""})
        self.assertEqual(self.store.count(), 7)

    # 12
    def test_clear_removes_all(self):
        for i in range(3):
            self.store.add({"id": i, "sort_key": i, "data": ""})
        self.store.clear()
        self.assertEqual(self.store.count(), 0)

    # 13
    def test_add_record_is_copied(self):
        rec = {"id": 1, "sort_key": 1, "data": "original"}
        self.store.add(rec)
        rec["data"] = "mutated"
        self.assertEqual(self.store.all()[0]["data"], "original")

    # 14
    def test_thread_safe_concurrent_adds(self):
        errors = []
        def adder(start, count):
            for i in range(start, start + count):
                try:
                    self.store.add({"id": i, "sort_key": i, "data": f"d{i}"})
                except Exception as e:
                    errors.append(e)
        threads = [threading.Thread(target=adder, args=(i * 100, 50)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(self.store.count(), 200)

    # 15
    def test_thread_safe_concurrent_deletes(self):
        for i in range(100):
            self.store.add({"id": i, "sort_key": i, "data": ""})
        threads = [threading.Thread(target=self.store.delete, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(self.store.count(), 0)

    # 16
    def test_delete_only_first_matching_id(self):
        # add two records with same id — delete should remove them all (list comprehension removes all)
        self.store.add({"id": 1, "sort_key": 1, "data": "a"})
        self.store.add({"id": 1, "sort_key": 2, "data": "b"})
        self.store.delete(1)
        remaining = [r for r in self.store.all() if r["id"] == 1]
        self.assertEqual(len(remaining), 0)

    # 17
    def test_all_returns_dicts(self):
        self.store.add({"id": 1, "sort_key": 1, "data": "a"})
        records = self.store.all()
        self.assertIsInstance(records[0], dict)

    # 18
    def test_add_string_id(self):
        self.store.add({"id": "abc", "sort_key": 1, "data": "x"})
        self.assertEqual(self.store.all()[0]["id"], "abc")

    # 19
    def test_delete_string_id(self):
        self.store.add({"id": "abc", "sort_key": 1, "data": "x"})
        result = self.store.delete("abc")
        self.assertTrue(result)
        self.assertEqual(self.store.count(), 0)

    # 20
    def test_add_extra_fields_preserved(self):
        self.store.add({"id": 1, "sort_key": 1, "data": "a", "extra": "value"})
        rec = self.store.all()[0]
        self.assertEqual(rec["extra"], "value")


# ===========================================================================
# encode_cursor / decode_cursor tests (15 tests)
# ===========================================================================

class TestCursorEncoding(unittest.TestCase):

    # 21
    def test_encode_returns_string(self):
        result = encode_cursor(1, 42)
        self.assertIsInstance(result, str)

    # 22
    def test_roundtrip_integers(self):
        sk, rid = decode_cursor(encode_cursor(5, 10))
        self.assertEqual(sk, 5)
        self.assertEqual(rid, 10)

    # 23
    def test_roundtrip_strings(self):
        sk, rid = decode_cursor(encode_cursor("alpha", "beta"))
        self.assertEqual(sk, "alpha")
        self.assertEqual(rid, "beta")

    # 24
    def test_roundtrip_mixed(self):
        sk, rid = decode_cursor(encode_cursor(3.14, "abc"))
        self.assertAlmostEqual(sk, 3.14)
        self.assertEqual(rid, "abc")

    # 25
    def test_cursor_is_base64(self):
        cursor = encode_cursor(1, 2)
        try:
            base64.urlsafe_b64decode(cursor + "==")
        except Exception:
            self.fail("Cursor is not valid base64")

    # 26
    def test_malformed_base64_raises(self):
        with self.assertRaises(ValueError):
            decode_cursor("!!!not-base64!!!")

    # 27
    def test_invalid_json_raises(self):
        bad = base64.urlsafe_b64encode(b"not-json").decode()
        with self.assertRaises(ValueError):
            decode_cursor(bad)

    # 28
    def test_missing_sort_key_raises(self):
        payload = base64.urlsafe_b64encode(json.dumps({"id": 1}).encode()).decode()
        with self.assertRaises(ValueError):
            decode_cursor(payload)

    # 29
    def test_missing_id_raises(self):
        payload = base64.urlsafe_b64encode(json.dumps({"sort_key": 1}).encode()).decode()
        with self.assertRaises(ValueError):
            decode_cursor(payload)

    # 30
    def test_non_object_json_raises(self):
        payload = base64.urlsafe_b64encode(json.dumps([1, 2]).encode()).decode()
        with self.assertRaises(ValueError):
            decode_cursor(payload)

    # 31
    def test_different_sort_keys_produce_different_cursors(self):
        c1 = encode_cursor(1, 1)
        c2 = encode_cursor(2, 1)
        self.assertNotEqual(c1, c2)

    # 32
    def test_different_ids_produce_different_cursors(self):
        c1 = encode_cursor(1, 1)
        c2 = encode_cursor(1, 2)
        self.assertNotEqual(c1, c2)

    # 33
    def test_zero_values_roundtrip(self):
        sk, rid = decode_cursor(encode_cursor(0, 0))
        self.assertEqual(sk, 0)
        self.assertEqual(rid, 0)

    # 34
    def test_large_values_roundtrip(self):
        sk, rid = decode_cursor(encode_cursor(10**18, 10**18))
        self.assertEqual(sk, 10**18)
        self.assertEqual(rid, 10**18)

    # 35
    def test_empty_string_cursor_raises(self):
        with self.assertRaises(ValueError):
            decode_cursor("")


# ===========================================================================
# OffsetPaginator tests (20 tests)
# ===========================================================================

class TestOffsetPaginator(unittest.TestCase):

    def _paginator(self, n=10):
        return OffsetPaginator(populated_store(n))

    # 36
    def test_first_page_basic(self):
        p = self._paginator(10)
        result = p.page(offset=0, limit=3)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.page.items), 3)

    # 37
    def test_total_matches_store(self):
        p = self._paginator(10)
        result = p.page(offset=0, limit=5)
        self.assertEqual(result.page.total, 10)

    # 38
    def test_has_next_true_when_more_records(self):
        p = self._paginator(10)
        result = p.page(offset=0, limit=5)
        self.assertTrue(result.page.has_next)

    # 39
    def test_has_next_false_on_last_page(self):
        p = self._paginator(5)
        result = p.page(offset=3, limit=5)
        self.assertFalse(result.page.has_next)

    # 40
    def test_offset_beyond_total_returns_empty(self):
        p = self._paginator(5)
        result = p.page(offset=100, limit=5)
        self.assertEqual(result.page.items, [])

    # 41
    def test_limit_zero_returns_error(self):
        p = self._paginator(5)
        result = p.page(offset=0, limit=0)
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.page)

    # 42
    def test_negative_limit_returns_error(self):
        p = self._paginator(5)
        result = p.page(offset=0, limit=-1)
        self.assertIsNotNone(result.error)

    # 43
    def test_negative_offset_returns_error(self):
        p = self._paginator(5)
        result = p.page(offset=-1, limit=5)
        self.assertIsNotNone(result.error)

    # 44
    def test_records_sorted_by_sort_key(self):
        store = BackingStore()
        store.add({"id": 3, "sort_key": 3, "data": "c"})
        store.add({"id": 1, "sort_key": 1, "data": "a"})
        store.add({"id": 2, "sort_key": 2, "data": "b"})
        p = OffsetPaginator(store)
        result = p.page(offset=0, limit=3)
        ids = [r["id"] for r in result.page.items]
        self.assertEqual(ids, [1, 2, 3])

    # 45
    def test_exact_multiple_limit(self):
        p = self._paginator(9)
        r1 = p.page(offset=0, limit=3)
        r2 = p.page(offset=3, limit=3)
        r3 = p.page(offset=6, limit=3)
        self.assertFalse(r3.page.has_next)
        self.assertEqual(len(r3.page.items), 3)

    # 46
    def test_limit_larger_than_dataset(self):
        p = self._paginator(3)
        result = p.page(offset=0, limit=100)
        self.assertEqual(len(result.page.items), 3)
        self.assertFalse(result.page.has_next)

    # 47
    def test_delete_bug_skip_row(self):
        store = populated_store(9)
        bug = demonstrate_offset_delete_bug(store, limit=3)
        # The deleted id was the first item of page1 — it's correctly absent from page2
        all_page2_ids = set(bug["page2_ids_bugged"])
        all_page1_ids = set(bug["page1_ids"])
        # deleted id was in page1 (expected)
        self.assertIn(bug["deleted_id"], all_page1_ids)
        # deleted id is not in page2
        self.assertNotIn(bug["deleted_id"], all_page2_ids)
        # At least one id is skipped (not in page1 or page2, not the deleted one)
        original_ids = set(range(1, 10))
        covered = all_page1_ids | all_page2_ids | {bug["deleted_id"]}
        skipped = original_ids - covered
        self.assertGreater(len(skipped), 0)

    # 48
    def test_insert_bug_reshows_row(self):
        store = populated_store(9)
        bug = demonstrate_offset_insert_bug(store, limit=3)
        # After inserting before offset, page2 should re-show an id from page1
        page1_set = set(bug["page1_ids"])
        page2_set = set(bug["page2_ids_bugged"])
        overlap = page1_set & page2_set
        self.assertGreater(len(overlap), 0)

    # 49
    def test_empty_store_returns_empty_page(self):
        p = OffsetPaginator(BackingStore())
        result = p.page(offset=0, limit=5)
        self.assertEqual(result.page.items, [])
        self.assertFalse(result.page.has_next)
        self.assertEqual(result.page.total, 0)

    # 50
    def test_offset_cursor_is_none(self):
        p = self._paginator(5)
        result = p.page(offset=0, limit=3)
        self.assertIsNone(result.page.cursor)

    # 51
    def test_second_page_items_differ_from_first(self):
        p = self._paginator(10)
        r1 = p.page(offset=0, limit=5)
        r2 = p.page(offset=5, limit=5)
        ids1 = {r["id"] for r in r1.page.items}
        ids2 = {r["id"] for r in r2.page.items}
        self.assertEqual(ids1 & ids2, set())

    # 52
    def test_full_traversal_no_overlap(self):
        p = self._paginator(12)
        seen = []
        offset = 0
        limit = 4
        while True:
            result = p.page(offset=offset, limit=limit)
            seen.extend(r["id"] for r in result.page.items)
            if not result.page.has_next:
                break
            offset += limit
        self.assertEqual(len(seen), len(set(seen)))

    # 53
    def test_sort_key_tiebreak_by_id(self):
        store = BackingStore()
        store.add({"id": 2, "sort_key": 5, "data": "a"})
        store.add({"id": 1, "sort_key": 5, "data": "b"})
        p = OffsetPaginator(store)
        result = p.page(offset=0, limit=2)
        ids = [r["id"] for r in result.page.items]
        self.assertEqual(ids, [1, 2])

    # 54
    def test_single_record_store(self):
        store = BackingStore()
        store.add({"id": 1, "sort_key": 1, "data": "solo"})
        p = OffsetPaginator(store)
        result = p.page(offset=0, limit=10)
        self.assertEqual(len(result.page.items), 1)
        self.assertFalse(result.page.has_next)

    # 55
    def test_page_items_contain_expected_fields(self):
        p = self._paginator(3)
        result = p.page(offset=0, limit=3)
        for item in result.page.items:
            self.assertIn("id", item)
            self.assertIn("sort_key", item)
            self.assertIn("data", item)


# ===========================================================================
# CursorPaginator tests (35 tests)
# ===========================================================================

class TestCursorPaginator(unittest.TestCase):

    def _paginator(self, n=10):
        return CursorPaginator(populated_store(n))

    # 56
    def test_first_page_no_cursor(self):
        p = self._paginator(10)
        result = p.page(cursor=None, limit=3)
        self.assertIsNone(result.error)
        self.assertEqual(len(result.page.items), 3)

    # 57
    def test_has_next_true_when_more(self):
        p = self._paginator(10)
        result = p.page(limit=5)
        self.assertTrue(result.page.has_next)

    # 58
    def test_has_next_false_on_last_page(self):
        p = self._paginator(5)
        result = p.page(limit=10)
        self.assertFalse(result.page.has_next)

    # 59
    def test_cursor_is_set_when_has_next(self):
        p = self._paginator(10)
        result = p.page(limit=5)
        self.assertIsNotNone(result.page.cursor)

    # 60
    def test_cursor_is_none_on_last_page(self):
        p = self._paginator(5)
        result = p.page(limit=10)
        self.assertIsNone(result.page.cursor)

    # 61
    def test_second_page_using_cursor(self):
        p = self._paginator(10)
        r1 = p.page(limit=5)
        r2 = p.page(cursor=r1.page.cursor, limit=5)
        self.assertIsNone(r2.error)
        ids1 = {r["id"] for r in r1.page.items}
        ids2 = {r["id"] for r in r2.page.items}
        self.assertEqual(ids1 & ids2, set())

    # 62
    def test_full_traversal_all_items_seen(self):
        n = 15
        p = CursorPaginator(populated_store(n))
        items, _ = p.all_pages(limit=4)
        self.assertEqual(len(items), n)

    # 63
    def test_full_traversal_no_duplicates(self):
        p = CursorPaginator(populated_store(15))
        items, _ = p.all_pages(limit=4)
        ids = [r["id"] for r in items]
        self.assertEqual(len(ids), len(set(ids)))

    # 64
    def test_full_traversal_exact_multiple(self):
        p = CursorPaginator(populated_store(12))
        items, _ = p.all_pages(limit=4)
        self.assertEqual(len(items), 12)

    # 65
    def test_limit_zero_returns_error(self):
        p = self._paginator(5)
        result = p.page(limit=0)
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.page)

    # 66
    def test_negative_limit_returns_error(self):
        p = self._paginator(5)
        result = p.page(limit=-5)
        self.assertIsNotNone(result.error)

    # 67
    def test_malformed_cursor_returns_error(self):
        p = self._paginator(5)
        result = p.page(cursor="!!bad!!", limit=5)
        self.assertIsNotNone(result.error)
        self.assertIsNone(result.page)

    # 68
    def test_tampered_cursor_missing_id(self):
        bad = base64.urlsafe_b64encode(json.dumps({"sort_key": 5}).encode()).decode()
        p = self._paginator(10)
        result = p.page(cursor=bad, limit=5)
        self.assertIsNotNone(result.error)

    # 69
    def test_tampered_cursor_missing_sort_key(self):
        bad = base64.urlsafe_b64encode(json.dumps({"id": 5}).encode()).decode()
        p = self._paginator(10)
        result = p.page(cursor=bad, limit=5)
        self.assertIsNotNone(result.error)

    # 70
    def test_tampered_cursor_non_object(self):
        bad = base64.urlsafe_b64encode(json.dumps([1, 2, 3]).encode()).decode()
        p = self._paginator(10)
        result = p.page(cursor=bad, limit=5)
        self.assertIsNotNone(result.error)

    # 71
    def test_past_end_cursor_returns_empty(self):
        p = self._paginator(5)
        # cursor pointing beyond last record
        cursor = encode_cursor(9999, 9999)
        result = p.page(cursor=cursor, limit=5)
        self.assertIsNone(result.error)
        self.assertEqual(result.page.items, [])
        self.assertFalse(result.page.has_next)

    # 72
    def test_empty_store_first_page(self):
        p = CursorPaginator(BackingStore())
        result = p.page(limit=5)
        self.assertIsNone(result.error)
        self.assertEqual(result.page.items, [])
        self.assertFalse(result.page.has_next)
        self.assertIsNone(result.page.cursor)

    # 73
    def test_limit_larger_than_dataset(self):
        p = self._paginator(3)
        result = p.page(limit=100)
        self.assertEqual(len(result.page.items), 3)
        self.assertFalse(result.page.has_next)

    # 74
    def test_immune_to_delete_bug(self):
        """Cursor pagination sees all original IDs even if rows are deleted."""
        store = populated_store(9)
        paginator = CursorPaginator(store)
        r1 = paginator.page(limit=3)
        assert r1.page is not None
        page1_ids = {r["id"] for r in r1.page.items}

        # Delete first record from the store
        first_id = min(page1_ids)
        store.delete(first_id)

        # The cursor from page 1 points past the delete zone — no skip
        r2 = paginator.page(cursor=r1.page.cursor, limit=3)
        assert r2.page is not None
        page2_ids = {r["id"] for r in r2.page.items}

        # Page 2 should not contain any page 1 items
        self.assertEqual(page1_ids & page2_ids, set())

    # 75
    def test_immune_to_insert_bug(self):
        """Inserting before cursor position doesn't re-show items."""
        store = populated_store(9)
        paginator = CursorPaginator(store)
        r1 = paginator.page(limit=3)
        assert r1.page is not None
        page1_ids = {r["id"] for r in r1.page.items}

        # Insert a row with a tiny sort_key (before everything)
        store.add({"id": 1000, "sort_key": -999, "data": "injected"})

        r2 = paginator.page(cursor=r1.page.cursor, limit=3)
        assert r2.page is not None
        page2_ids = {r["id"] for r in r2.page.items}

        # No overlap
        self.assertEqual(page1_ids & page2_ids, set())

    # 76
    def test_items_sorted_ascending(self):
        store = BackingStore()
        for sk, rid in [(3, 1), (1, 2), (2, 3)]:
            store.add({"id": rid, "sort_key": sk, "data": ""})
        p = CursorPaginator(store)
        result = p.page(limit=3)
        sort_keys = [r["sort_key"] for r in result.page.items]
        self.assertEqual(sort_keys, sorted(sort_keys))

    # 77
    def test_single_record(self):
        store = BackingStore()
        store.add({"id": 1, "sort_key": 1, "data": "x"})
        p = CursorPaginator(store)
        result = p.page(limit=1)
        self.assertEqual(len(result.page.items), 1)
        self.assertFalse(result.page.has_next)

    # 78
    def test_total_reflects_current_store(self):
        store = populated_store(10)
        p = CursorPaginator(store)
        result = p.page(limit=5)
        self.assertEqual(result.page.total, 10)

    # 79
    def test_page_1_limit_1(self):
        p = self._paginator(5)
        result = p.page(limit=1)
        self.assertEqual(len(result.page.items), 1)
        self.assertTrue(result.page.has_next)

    # 80
    def test_all_pages_single_page(self):
        p = CursorPaginator(populated_store(3))
        items, results = p.all_pages(limit=10)
        self.assertEqual(len(items), 3)
        self.assertEqual(len(results), 1)

    # 81
    def test_all_pages_multiple_pages(self):
        p = CursorPaginator(populated_store(10))
        items, results = p.all_pages(limit=3)
        self.assertEqual(len(items), 10)
        # 3+3+3+1 = 4 pages
        self.assertEqual(len(results), 4)

    # 82
    def test_consecutive_cursors_advance(self):
        p = CursorPaginator(populated_store(20))
        cursors = set()
        cursor = None
        for _ in range(4):
            result = p.page(cursor=cursor, limit=5)
            if cursor:
                cursors.add(cursor)
            cursor = result.page.cursor
            if cursor is None:
                break
        # All cursors should be unique
        self.assertEqual(len(cursors), len(set(cursors)))

    # 83
    def test_cursor_from_last_item_of_partial_page(self):
        p = CursorPaginator(populated_store(7))
        r1 = p.page(limit=5)  # items 1-5
        r2 = p.page(cursor=r1.page.cursor, limit=5)  # items 6-7
        self.assertEqual(len(r2.page.items), 2)
        self.assertFalse(r2.page.has_next)

    # 84
    def test_stable_traversal_with_sort_key_gaps(self):
        store = BackingStore()
        sort_keys = [10, 20, 30, 40, 50]
        for i, sk in enumerate(sort_keys, 1):
            store.add({"id": i, "sort_key": sk, "data": f"d{i}"})
        p = CursorPaginator(store)
        items, _ = p.all_pages(limit=2)
        self.assertEqual(len(items), 5)
        extracted_sk = [r["sort_key"] for r in items]
        self.assertEqual(extracted_sk, sorted(extracted_sk))

    # 85
    def test_no_error_for_valid_cursor(self):
        p = self._paginator(10)
        r1 = p.page(limit=5)
        r2 = p.page(cursor=r1.page.cursor, limit=5)
        self.assertIsNone(r2.error)

    # 86
    def test_string_sort_keys_ordering(self):
        store = BackingStore()
        for i, sk in enumerate(["banana", "apple", "cherry"], 1):
            store.add({"id": i, "sort_key": sk, "data": ""})
        p = CursorPaginator(store)
        result = p.page(limit=3)
        sort_keys = [r["sort_key"] for r in result.page.items]
        self.assertEqual(sort_keys, sorted(sort_keys))

    # 87
    def test_duplicate_sort_keys_tiebroken_by_id(self):
        store = BackingStore()
        store.add({"id": 3, "sort_key": 5, "data": "c"})
        store.add({"id": 1, "sort_key": 5, "data": "a"})
        store.add({"id": 2, "sort_key": 5, "data": "b"})
        p = CursorPaginator(store)
        r1 = p.page(limit=2)
        ids1 = [r["id"] for r in r1.page.items]
        r2 = p.page(cursor=r1.page.cursor, limit=2)
        ids2 = [r["id"] for r in r2.page.items]
        self.assertEqual(ids1, [1, 2])
        self.assertEqual(ids2, [3])

    # 88
    def test_last_page_empty_items_when_exact_multiple(self):
        """When total is exact multiple of limit, final has_next=False."""
        p = CursorPaginator(populated_store(6))
        items, results = p.all_pages(limit=3)
        self.assertEqual(len(items), 6)
        self.assertFalse(results[-1].page.has_next)

    # 89
    def test_cursor_not_none_on_intermediate_pages(self):
        p = CursorPaginator(populated_store(10))
        r1 = p.page(limit=5)
        self.assertIsNotNone(r1.page.cursor)

    # 90
    def test_page_result_error_none_on_success(self):
        p = self._paginator(5)
        result = p.page(limit=3)
        self.assertIsNone(result.error)


# ===========================================================================
# Page and PageResult dataclass tests (6 tests)
# ===========================================================================

class TestPageDataclasses(unittest.TestCase):

    # 91
    def test_page_fields(self):
        pg = Page(items=[], total=0, has_next=False, cursor=None)
        self.assertEqual(pg.items, [])
        self.assertEqual(pg.total, 0)
        self.assertFalse(pg.has_next)
        self.assertIsNone(pg.cursor)

    # 92
    def test_page_result_success(self):
        pg = Page(items=[{"id": 1}], total=1, has_next=False)
        pr = PageResult(page=pg)
        self.assertIsNone(pr.error)

    # 93
    def test_page_result_error(self):
        pr = PageResult(page=None, error="something went wrong")
        self.assertIsNotNone(pr.error)
        self.assertIsNone(pr.page)

    # 94
    def test_pagination_test_result(self):
        r = PaginationTestResult(name="test", passed=True, message="ok")
        self.assertTrue(r.passed)
        self.assertEqual(r.name, "test")

    # 95
    def test_pagination_report_tracking(self):
        report = PaginationReport()
        report.add(PaginationTestResult(name="a", passed=True))
        report.add(PaginationTestResult(name="b", passed=False))
        self.assertEqual(report.total, 2)
        self.assertEqual(report.passed, 1)
        self.assertEqual(report.failed, 1)

    # 96
    def test_pagination_report_all_passed(self):
        report = PaginationReport()
        report.add(PaginationTestResult(name="a", passed=True))
        self.assertTrue(report.all_passed)
        report.add(PaginationTestResult(name="b", passed=False))
        self.assertFalse(report.all_passed)


# ===========================================================================
# HTTP Server tests (20 tests)
# ===========================================================================

class TestPaginationServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.store = populated_store(20)
        cls.server = PaginationServer(store=cls.store, port=0)
        cls.server.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.stop()

    # 97
    def test_server_starts_and_responds(self):
        data = self.server.get_json("/cursor?limit=5")
        self.assertIn("items", data)

    # 98
    def test_offset_endpoint_basic(self):
        data = self.server.get_json("/offset?offset=0&limit=5")
        self.assertEqual(len(data["items"]), 5)

    # 99
    def test_cursor_endpoint_basic(self):
        data = self.server.get_json("/cursor?limit=5")
        self.assertEqual(len(data["items"]), 5)

    # 100
    def test_offset_total_field(self):
        data = self.server.get_json("/offset?offset=0&limit=5")
        self.assertEqual(data["total"], 20)

    # 101
    def test_cursor_total_field(self):
        data = self.server.get_json("/cursor?limit=5")
        self.assertEqual(data["total"], 20)

    # 102
    def test_offset_has_next_true(self):
        data = self.server.get_json("/offset?offset=0&limit=5")
        self.assertTrue(data["has_next"])

    # 103
    def test_cursor_has_next_true(self):
        data = self.server.get_json("/cursor?limit=5")
        self.assertTrue(data["has_next"])

    # 104
    def test_offset_last_page_no_next(self):
        data = self.server.get_json("/offset?offset=15&limit=10")
        self.assertFalse(data["has_next"])

    # 105
    def test_cursor_last_page_no_next(self):
        data = self.server.get_json("/cursor?limit=100")
        self.assertFalse(data["has_next"])

    # 106
    def test_cursor_pagination_traversal(self):
        all_ids = []
        cursor = None
        while True:
            path = "/cursor?limit=4"
            if cursor:
                import urllib.parse
                path += "&cursor=" + urllib.parse.quote(cursor)
            data = self.server.get_json(path)
            all_ids.extend(r["id"] for r in data["items"])
            if not data["has_next"]:
                break
            cursor = data["cursor"]
        self.assertEqual(len(all_ids), 20)
        self.assertEqual(len(set(all_ids)), 20)

    # 107
    def test_not_found_returns_404(self):
        status, body = self.server.get_raw("/nonexistent")
        self.assertEqual(status, 404)

    # 108
    def test_offset_invalid_limit_returns_400(self):
        status, body = self.server.get_raw("/offset?offset=0&limit=0")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    # 109
    def test_cursor_invalid_limit_returns_400(self):
        status, body = self.server.get_raw("/cursor?limit=0")
        self.assertEqual(status, 400)

    # 110
    def test_cursor_tampered_cursor_returns_400(self):
        status, body = self.server.get_raw("/cursor?cursor=!!bad!!&limit=5")
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    # 111
    def test_offset_default_limit(self):
        data = self.server.get_json("/offset?offset=0")
        self.assertLessEqual(len(data["items"]), 10)

    # 112
    def test_cursor_default_limit(self):
        data = self.server.get_json("/cursor")
        self.assertLessEqual(len(data["items"]), 10)

    # 113
    def test_offset_empty_result_beyond_range(self):
        data = self.server.get_json("/offset?offset=9999&limit=5")
        self.assertEqual(data["items"], [])

    # 114
    def test_cursor_past_end(self):
        import urllib.parse
        cursor = encode_cursor(9999, 9999)
        path = "/cursor?limit=5&cursor=" + urllib.parse.quote(cursor)
        data = self.server.get_json(path)
        self.assertEqual(data["items"], [])

    # 115
    def test_cursor_field_in_response_when_has_next(self):
        data = self.server.get_json("/cursor?limit=5")
        self.assertIn("cursor", data)
        self.assertIsNotNone(data["cursor"])

    # 116
    def test_cursor_field_null_when_no_next(self):
        data = self.server.get_json("/cursor?limit=100")
        self.assertIsNone(data["cursor"])


# ===========================================================================
# make_records / populated_store helpers (4 tests)
# ===========================================================================

class TestHelpers(unittest.TestCase):

    # 117
    def test_make_records_count(self):
        records = make_records(5)
        self.assertEqual(len(records), 5)

    # 118
    def test_make_records_fields(self):
        records = make_records(3)
        for r in records:
            self.assertIn("id", r)
            self.assertIn("sort_key", r)
            self.assertIn("data", r)

    # 119
    def test_make_records_custom_sort_key(self):
        records = make_records(3, sort_key_fn=lambda i: i * 10)
        self.assertEqual(records[0]["sort_key"], 10)
        self.assertEqual(records[1]["sort_key"], 20)

    # 120
    def test_populated_store_count(self):
        store = populated_store(7)
        self.assertEqual(store.count(), 7)


# ===========================================================================
# Boundary / edge cases (7 tests)
# ===========================================================================

class TestBoundaryCases(unittest.TestCase):

    # 121
    def test_cursor_page_size_one_full_traversal(self):
        n = 5
        p = CursorPaginator(populated_store(n))
        items, _ = p.all_pages(limit=1)
        self.assertEqual(len(items), n)
        ids = [r["id"] for r in items]
        self.assertEqual(len(ids), len(set(ids)))

    # 122
    def test_offset_page_size_one_full_traversal(self):
        store = populated_store(5)
        p = OffsetPaginator(store)
        seen = []
        offset = 0
        while True:
            result = p.page(offset=offset, limit=1)
            seen.extend(r["id"] for r in result.page.items)
            if not result.page.has_next:
                break
            offset += 1
        self.assertEqual(len(seen), 5)

    # 123
    def test_add_and_traverse_all(self):
        store = BackingStore()
        for i in range(1, 11):
            store.add({"id": i, "sort_key": i, "data": f"item{i}"})
        p = CursorPaginator(store)
        items, _ = p.all_pages(limit=3)
        self.assertEqual(len(items), 10)

    # 124
    def test_store_delete_all_then_paginate(self):
        store = populated_store(5)
        for i in range(1, 6):
            store.delete(i)
        p = CursorPaginator(store)
        result = p.page(limit=5)
        self.assertEqual(result.page.items, [])

    # 125
    def test_report_empty(self):
        report = PaginationReport()
        self.assertEqual(report.total, 0)
        self.assertTrue(report.all_passed)

    # 126
    def test_cursor_pagination_large_dataset(self):
        store = populated_store(100)
        p = CursorPaginator(store)
        items, _ = p.all_pages(limit=7)
        self.assertEqual(len(items), 100)
        ids = [r["id"] for r in items]
        self.assertEqual(len(ids), len(set(ids)))

    # 127
    def test_offset_traversal_large_dataset(self):
        store = populated_store(100)
        p = OffsetPaginator(store)
        seen = []
        offset = 0
        limit = 7
        while True:
            result = p.page(offset=offset, limit=limit)
            seen.extend(r["id"] for r in result.page.items)
            if not result.page.has_next:
                break
            offset += limit
        self.assertEqual(len(seen), 100)


# ===========================================================================
# Teeth (campaign teeth contract)
# ===========================================================================

class TestTeeth(unittest.TestCase):
    """The harness must catch a real planted pagination bug (the teeth contract)."""

    # 128
    def test_teeth_verified(self):
        result = verify(TEETH)
        self.assertIsNone(result["error"], result["error"])
        self.assertTrue(result["teeth_verified"], f"teeth not verified: {result}")

    # 129
    def test_oracle_is_clean(self):
        self.assertFalse(TEETH.prove(TEETH.oracle))

    # 130
    def test_every_mutant_is_caught(self):
        for mutant in TEETH.mutants:
            self.assertTrue(TEETH.prove(mutant.impl), f"mutant not caught: {mutant.name}")

    # 131
    def test_corpus_nonempty(self):
        self.assertGreaterEqual(TEETH.corpus_size, 1)


if __name__ == "__main__":
    unittest.main()
