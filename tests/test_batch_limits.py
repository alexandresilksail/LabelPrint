"""批量生成上限、进度、取消测试（审计发现 F-04）。

规格：设定可配置的单批数量上限；拒绝负数、零、非整数及超限请求；
提供进度反馈；支持安全取消；单事务失败整体回滚（无半成品）。
"""

import tempfile
import unittest

from client.barcode.service import BarcodeError, BarcodeService
from client.database.db import Database


class BatchTestCase(unittest.TestCase):
    def make_service(self, **kwargs):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        db = Database(td.name + "/codelabel.db")
        self.addCleanup(db.close)
        return db, BarcodeService(db, **kwargs)

    def make_range(self, svc, start=100000000, end=100000999):
        return svc.create_range("批量范围", "EAN-13", "950", start, end)


class TestBatchLimits(BatchTestCase):
    def test_invalid_counts_rejected(self):
        _, svc = self.make_service()
        rid = self.make_range(svc)
        for bad in (0, -1, -100, True, False, 3.0, "5", None, [3]):
            with self.assertRaises(BarcodeError, msg=f"count={bad!r} 应被拒绝"):
                svc.generate_batch("EAN-13", count=bad, range_id=rid)

    def test_over_default_limit_rejected(self):
        _, svc = self.make_service()
        rid = self.make_range(svc, start=100000000, end=999999999)
        with self.assertRaises(BarcodeError) as ctx:
            svc.generate_batch("EAN-13", count=10001, range_id=rid)
        self.assertIn("上限", str(ctx.exception))

    def test_custom_limit_enforced(self):
        _, svc = self.make_service(max_batch_size=5)
        rid = self.make_range(svc)
        with self.assertRaises(BarcodeError):
            svc.generate_batch("EAN-13", count=6, range_id=rid)
        result = svc.generate_batch("EAN-13", count=5, range_id=rid)
        self.assertEqual(len(result["created"]), 5)

    def test_invalid_max_batch_size_rejected(self):
        db = Database(tempfile.mkdtemp() + "/x.db")
        self.addCleanup(db.close)
        for bad in (0, -1, True, 2.5, "10"):
            with self.assertRaises(BarcodeError, msg=f"max_batch_size={bad!r} 应被拒绝"):
                BarcodeService(db, max_batch_size=bad)


class TestBatchProgress(BatchTestCase):
    def test_progress_callback_monotonic(self):
        _, svc = self.make_service()
        rid = self.make_range(svc)
        calls = []
        result = svc.generate_batch(
            "EAN-13", count=10, range_id=rid,
            progress_callback=lambda done, total: calls.append((done, total)),
        )
        self.assertEqual(len(result["created"]), 10)
        self.assertEqual(len(calls), 10)
        self.assertEqual([c[0] for c in calls], list(range(1, 11)))
        self.assertTrue(all(c[1] == 10 for c in calls))


class TestBatchCancel(BatchTestCase):
    def test_cancel_after_n(self):
        _, svc = self.make_service()
        rid = self.make_range(svc)
        state = {"n": 0}

        def cancel():
            return state["n"] >= 3

        def progress(done, total):
            state["n"] = done

        result = svc.generate_batch(
            "EAN-13", count=50, range_id=rid,
            progress_callback=progress, should_cancel=cancel,
        )
        self.assertTrue(result["canceled"])
        self.assertEqual(len(result["created"]), 3)
        self.assertEqual(result["requested"], 50)
        # 已生成部分全部有效、无重复
        values = [r["barcode_value"] for r in result["created"]]
        self.assertEqual(len(set(values)), 3)
        for v in values:
            rec = svc.get_record(v)
            self.assertEqual(rec["allocation_status"], "generated")
            self.assertEqual(rec["range_id"], rid)
        # 范围指针精确前进 3
        self.assertEqual(svc.get_range(rid)["next_number"], 100000003)
        self.assertEqual(svc.get_stats()["total"], 3)

    def test_cancel_immediately(self):
        _, svc = self.make_service()
        rid = self.make_range(svc)
        result = svc.generate_batch(
            "EAN-13", count=10, range_id=rid,
            should_cancel=lambda: True,
        )
        self.assertTrue(result["canceled"])
        self.assertEqual(len(result["created"]), 0)
        self.assertEqual(svc.get_stats()["total"], 0)
        self.assertEqual(svc.get_range(rid)["next_number"], 100000000)
        # 取消后可继续正常生成
        result2 = svc.generate_batch("EAN-13", count=2, range_id=rid)
        self.assertEqual(len(result2["created"]), 2)
        self.assertFalse(result2["canceled"])

    def test_cancel_without_callback_completes(self):
        _, svc = self.make_service()
        rid = self.make_range(svc)
        result = svc.generate_batch("EAN-13", count=5, range_id=rid)
        self.assertFalse(result["canceled"])
        self.assertFalse(result["exhausted"])
        self.assertEqual(len(result["created"]), 5)


class TestBatchExhaustionWithFlags(BatchTestCase):
    def test_exhaustion_reports_flag(self):
        _, svc = self.make_service()
        rid = self.make_range(svc, start=100000000, end=100000004)
        result = svc.generate_batch("EAN-13", count=10, range_id=rid)
        self.assertTrue(result["exhausted"])
        self.assertFalse(result["canceled"])
        self.assertEqual(len(result["created"]), 5)
        self.assertEqual(svc.get_range(rid)["range_status"], "exhausted")


if __name__ == "__main__":
    unittest.main()
