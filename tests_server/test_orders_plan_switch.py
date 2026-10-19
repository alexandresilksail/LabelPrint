"""审计问题 2：订阅订单方案复用错误。

覆盖：
- 同一方案重复下单 → 复用同一订单（幂等），不产生新订单与重复事件；
- 月付切年付 / 年付切月付 → 旧订单取消并保留历史，新订单使用新方案；
- 计划价格变更后 → 旧订单不得复用（金额不一致）；
- 已终态订单 → 不复用，创建新订单；
- 并发创建 → 数据库部分唯一索引兜底，最终只有一条待付款订单。
"""

import threading
import unittest

from sqlalchemy import select

from server.app.database import SessionLocal
from server.app.models import AuditLog, Order, PaymentEvent, Plan, User
from server.services.orders import OrderError, create_order

from .base import ServerTestCase


class OrderPlanSwitchTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.seed_plans()
        self.user_dict, self.token = self.register_verified()
        self.user_id = self.user_dict["id"]

    # ---------- 辅助 ----------
    def plan(self, code: str) -> Plan:
        with SessionLocal() as db:
            return db.scalar(select(Plan).where(Plan.plan_code == code))

    def orders_of_user(self) -> list[Order]:
        with SessionLocal() as db:
            return list(db.scalars(
                select(Order).where(Order.user_id == self.user_id)
                .order_by(Order.id)))

    def create(self, plan_code: str) -> dict:
        r = self.client.post("/api/orders", json={"plan_code": plan_code},
                             headers=self.auth(self.token))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    # ---------- 用例 ----------
    def test_same_plan_is_idempotent(self):
        first = self.create("monthly")
        second = self.create("monthly")
        self.assertEqual(first["id"], second["id"])

        orders = self.orders_of_user()
        self.assertEqual(len(orders), 1)
        with SessionLocal() as db:
            events = list(db.scalars(select(PaymentEvent).where(
                PaymentEvent.order_id == first["id"])))
        self.assertEqual(len(events), 1, "重复下单不应重复写 order_created 事件")

    def test_monthly_to_yearly_creates_new_order(self):
        monthly = self.create("monthly")
        yearly = self.create("yearly")

        self.assertNotEqual(monthly["id"], yearly["id"],
                            "切换方案后不得返回旧方案的订单")
        self.assertEqual(yearly["plan_code"], "yearly")
        self.assertEqual(yearly["amount"], "199.00")

        orders = {o.id: o for o in self.orders_of_user()}
        self.assertEqual(orders[monthly["id"]].order_status, "canceled",
                         "旧订单应被取消并保留历史")
        self.assertEqual(orders[yearly["id"]].order_status, "pending")

    def test_yearly_to_monthly_creates_new_order(self):
        yearly = self.create("yearly")
        monthly = self.create("monthly")
        self.assertNotEqual(yearly["id"], monthly["id"])
        self.assertEqual(monthly["plan_code"], "monthly")
        self.assertEqual(monthly["amount"], "29.00")
        orders = {o.id: o for o in self.orders_of_user()}
        self.assertEqual(orders[yearly["id"]].order_status, "canceled")

    def test_switch_writes_cancel_event_and_audit(self):
        monthly = self.create("monthly")
        self.create("yearly")
        with SessionLocal() as db:
            events = [e.event_type for e in db.scalars(select(PaymentEvent).where(
                PaymentEvent.order_id == monthly["id"]))]
        self.assertIn("order_canceled", events)

        with SessionLocal() as db:
            logs = list(db.scalars(select(AuditLog).where(
                AuditLog.action == "order_canceled_on_plan_change")))
        self.assertEqual(len(logs), 1)

    def test_price_change_invalidates_reuse(self):
        monthly = self.create("monthly")
        with SessionLocal() as db:
            plan = db.scalar(select(Plan).where(Plan.plan_code == "monthly"))
            plan.price = 39
            db.commit()
        again = self.create("monthly")
        self.assertNotEqual(monthly["id"], again["id"],
                            "金额已变更，不得复用旧价格的订单")
        self.assertEqual(again["amount"], "39.00")

    def test_terminal_order_not_reused(self):
        monthly = self.create("monthly")
        with SessionLocal() as db:
            order = db.get(Order, monthly["id"])
            order.order_status = "canceled"
            db.commit()
        again = self.create("monthly")
        self.assertNotEqual(monthly["id"], again["id"])
        self.assertEqual(again["order_status"], "pending")

    def test_only_one_pending_order_after_switch_cycle(self):
        self.create("monthly")
        self.create("yearly")
        self.create("monthly")
        pending = [o for o in self.orders_of_user()
                   if o.order_status == "pending"]
        self.assertEqual(len(pending), 1, "任何时刻只允许一条待付款订单")
        self.assertEqual(len(self.orders_of_user()), 3, "历史订单应保留")


class OrderConcurrencyTests(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.seed_plans()
        self.user_dict, self.token = self.register_verified()
        self.user_id = self.user_dict["id"]

    def _create_in_thread(self, plan_code: str, results: list, index: int):
        try:
            with SessionLocal() as db:
                user = db.get(User, self.user_id)
                order = create_order(db, user, plan_code=plan_code)
                results[index] = ("ok", order.id)
        except OrderError as exc:
            results[index] = ("error", exc.status_code)
        except Exception as exc:                       # noqa: BLE001
            results[index] = ("exception", repr(exc))

    def test_concurrent_creation_single_pending(self):
        """并发下单：数据库唯一索引兜底，最终只有一条 pending 订单。"""
        results = [None] * 6
        barrier = threading.Barrier(len(results))
        threads = []
        for i in range(len(results)):
            def run(idx=i):
                barrier.wait()
                self._create_in_thread("monthly", results, idx)
            threads.append(threading.Thread(target=run))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertNotIn("exception", [r[0] for r in results if r],
                         f"并发出现未捕获异常：{results}")
        with SessionLocal() as db:
            orders = list(db.scalars(
                select(Order).where(Order.user_id == self.user_id)))
        self.assertEqual(len(orders), 1,
                         f"并发创建出现多条订单：{[(o.id, o.order_status) for o in orders]}")
        self.assertEqual(orders[0].order_status, "pending")
        ok_ids = {r[1] for r in results if r and r[0] == "ok"}
        self.assertEqual(ok_ids, {orders[0].id},
                         "所有并发请求应复用同一订单")


if __name__ == "__main__":
    unittest.main()
