"""审计问题 4：付款确认的事务与通知一致性。

覆盖：
- 订单确认、订阅激活、支付事件、审计日志、通知 outbox 同事务提交；
- 邮件发送失败不影响订单状态，outbox 记录失败并可重试至成功；
- 数据库提交失败 → 整体回滚，订单仍为 pending，无 outbox / 审计残留；
- 重复确认被拒且不产生第二条通知；
- 并发确认只有一个成功；
- outbox 去重与指数退避、达到上限标记 dead。
"""

import threading
import unittest
import unittest.mock
from datetime import timedelta

from sqlalchemy import select

from server.app import mailer as mailer_module
from server.app.database import SessionLocal
from server.app.models import (
    AuditLog, Order, OutboxMessage, PaymentEvent, Subscription, User,
)
from server.app.security import utc_now
from server.services import outbox
from server.services.orders import OrderError, confirm_payment

from .base import ServerTestCase


class PaymentOutboxTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.seed_plans()
        self.user_dict, self.token = self.register_verified()
        self.user_id = self.user_dict["id"]
        self.admin_dict, self.admin_token = self.create_admin()
        r = self.client.post("/api/orders", json={"plan_code": "monthly"},
                             headers=self.auth(self.token))
        self.assertEqual(r.status_code, 201, r.text)
        self.order_id = r.json()["id"]

    # ---------- 辅助 ----------
    def confirm(self, approved=True, reason="收款已核实", order_id=None):
        oid = order_id or self.order_id
        return self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            json={"approved": approved, "reason": reason},
            headers=self.auth(self.admin_token))

    def outbox_rows(self) -> list[OutboxMessage]:
        with SessionLocal() as db:
            return list(db.scalars(select(OutboxMessage)))

    def order(self) -> Order:
        with SessionLocal() as db:
            return db.get(Order, self.order_id)

    # ---------- 用例 ----------
    def test_confirm_commits_all_records_together(self):
        self.mailer.clear()          # 清掉注册验证邮件，只统计付款通知
        r = self.confirm()
        self.assertEqual(r.status_code, 200, r.text)

        order = self.order()
        self.assertEqual(order.order_status, "confirmed")
        self.assertIsNotNone(order.confirmed_at)

        with SessionLocal() as db:
            events = [e.event_type for e in db.scalars(
                select(PaymentEvent).where(PaymentEvent.order_id == self.order_id))]
            sub = db.scalar(select(Subscription).where(
                Subscription.user_id == self.user_id))
            logs = list(db.scalars(select(AuditLog).where(
                AuditLog.action == "confirm_payment")))
        self.assertIn("payment_confirmed", events)
        self.assertIsNotNone(sub)
        self.assertEqual(sub.status, "active")
        self.assertEqual(len(logs), 1)

        rows = self.outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "sent", "正常情况下邮件应立即投递成功")
        self.assertEqual(len(self.mailer.sent), 1)

    def test_mail_failure_does_not_break_order(self):
        with unittest.mock.patch.object(
                mailer_module, "send_mail",
                side_effect=RuntimeError("SMTP 连接被拒绝")):
            r = self.confirm()
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.order().order_status, "confirmed")

        rows = self.outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].status, "failed")
        self.assertEqual(rows[0].attempts, 1)
        self.assertIn("SMTP", rows[0].last_error)
        self.assertGreater(rows[0].next_attempt_at, utc_now() - timedelta(seconds=1))

        # 邮件服务恢复后重试成功
        with SessionLocal() as db:
            stats = outbox.dispatch_pending(
                db, now=utc_now() + timedelta(hours=1))
        self.assertEqual(stats["sent"], 1)
        row = self.outbox_rows()[0]
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.attempts, 2)

    def test_mail_returning_false_counts_as_failure(self):
        with unittest.mock.patch.object(mailer_module, "send_mail",
                                        return_value=False):
            r = self.confirm()
        self.assertEqual(r.status_code, 200, r.text)
        rows = self.outbox_rows()
        self.assertEqual(rows[0].status, "failed")
        self.assertIn("返回失败", rows[0].last_error)

    def test_commit_failure_rolls_back_everything(self):
        # 注册验证时已建立 7 天试用订阅，这里记录基线，断言失败后未被改写
        with SessionLocal() as db:
            before = db.scalar(select(Subscription).where(
                Subscription.user_id == self.user_id))
            baseline = (before.status, before.plan_id, before.expires_at,
                        before.started_at)

        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            with unittest.mock.patch.object(
                    db, "commit", side_effect=RuntimeError("磁盘写入失败")):
                with self.assertRaises(OrderError) as ctx:
                    confirm_payment(db, order_id=self.order_id, admin=admin,
                                    approved=True, reason="收款已核实")
        self.assertEqual(ctx.exception.status_code, 500)

        self.assertEqual(self.order().order_status, "pending",
                         "提交失败后订单不得停留在已确认状态")
        with SessionLocal() as db:
            events = [e.event_type for e in db.scalars(
                select(PaymentEvent).where(PaymentEvent.order_id == self.order_id))]
            self.assertNotIn("payment_confirmed", events)
            sub = db.scalar(select(Subscription).where(
                Subscription.user_id == self.user_id))
        self.assertIsNotNone(sub, "原有试用订阅不应被删除")
        self.assertEqual(
            (sub.status, sub.plan_id, sub.expires_at, sub.started_at),
            baseline, "订阅不得在订单事务失败后被改写为付费订阅")
        self.assertEqual(self.outbox_rows(), [],
                         "事务回滚后不应残留 outbox 记录")

    def test_duplicate_confirm_rejected_without_extra_notification(self):
        first = self.confirm()
        self.assertEqual(first.status_code, 200, first.text)
        second = self.confirm()
        self.assertEqual(second.status_code, 409)
        self.assertEqual(len(self.outbox_rows()), 1)
        with SessionLocal() as db:
            events = [e.event_type for e in db.scalars(
                select(PaymentEvent).where(PaymentEvent.order_id == self.order_id))]
        self.assertEqual(events.count("payment_confirmed"), 1)

    def test_concurrent_confirm_only_one_wins(self):
        results: list = [None, None]
        barrier = threading.Barrier(2)

        def worker(idx):
            barrier.wait()
            with SessionLocal() as db:
                admin = db.scalar(select(User).where(User.username == "admin"))
                try:
                    confirm_payment(db, order_id=self.order_id, admin=admin,
                                    approved=True, reason=f"并发确认 {idx}")
                    results[idx] = "ok"
                except OrderError as exc:
                    results[idx] = exc.status_code
                except Exception as exc:               # noqa: BLE001
                    results[idx] = repr(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertIn("ok", results, f"并发确认无一成功：{results}")
        self.assertEqual(self.order().order_status, "confirmed")
        self.assertEqual(len(self.outbox_rows()), 1,
                         f"重复通知：{[(r.dedupe_key, r.status) for r in self.outbox_rows()]}")
        with SessionLocal() as db:
            events = [e.event_type for e in db.scalars(
                select(PaymentEvent).where(PaymentEvent.order_id == self.order_id))]
        self.assertEqual(events.count("payment_confirmed"), 1)

    def test_rejection_notifies_and_is_terminal(self):
        r = self.confirm(approved=False, reason="凭证与收款不符")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.order().order_status, "failed")
        rows = self.outbox_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].topic, "payment_result")
        # 已终态：再次确认（无论通过还是拒绝）都被拒
        self.assertEqual(self.confirm().status_code, 409)
        self.assertEqual(self.confirm(approved=False).status_code, 409)

    def test_order_not_pending_cannot_be_confirmed(self):
        with SessionLocal() as db:
            order = db.get(Order, self.order_id)
            order.order_status = "canceled"
            db.commit()
        self.assertEqual(self.confirm().status_code, 409)


class OutboxUnitTests(ServerTestCase):
    def test_enqueue_dedupe(self):
        with SessionLocal() as db:
            first = outbox.enqueue(db, dedupe_key="k1", topic="t",
                                   recipient="a@example.com", subject="s",
                                   body_text="b")
            db.commit()
            second = outbox.enqueue(db, dedupe_key="k1", topic="t",
                                    recipient="a@example.com", subject="s",
                                    body_text="b")
            db.commit()
            self.assertEqual(first.id, second.id)
        with SessionLocal() as db:
            self.assertEqual(len(list(db.scalars(select(OutboxMessage)))), 1)

    def test_retry_backoff_and_dead_letter(self):
        with SessionLocal() as db:
            outbox.enqueue(db, dedupe_key="k2", topic="t",
                           recipient="a@example.com", subject="s", body_text="b")
            db.commit()

        with unittest.mock.patch.object(mailer_module, "send_mail",
                                        side_effect=RuntimeError("down")):
            for attempt in range(outbox.MAX_ATTEMPTS):
                with SessionLocal() as db:
                    outbox.dispatch_pending(
                        db, now=utc_now() + timedelta(days=attempt + 1))
        with SessionLocal() as db:
            msg = db.scalar(select(OutboxMessage).where(
                OutboxMessage.dedupe_key == "k2"))
        self.assertEqual(msg.attempts, outbox.MAX_ATTEMPTS)
        self.assertEqual(msg.status, "dead",
                         "达到重试上限应标记 dead 保留现场")

    def test_pending_count(self):
        with SessionLocal() as db:
            self.assertEqual(outbox.pending_count(db), 0)
            outbox.enqueue(db, dedupe_key="k3", topic="t",
                           recipient="a@example.com", subject="s", body_text="b")
            db.commit()
            self.assertEqual(outbox.pending_count(db), 1)


if __name__ == "__main__":
    unittest.main()
