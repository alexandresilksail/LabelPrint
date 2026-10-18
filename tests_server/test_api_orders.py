"""订单 API 集成测试：计划 / 下单 / 复用待付款 / 付款凭证 / 管理员确认。

覆盖：正常购买闭环、非管理员确认被拒、重复确认被拒、
webhook 未配置签名一律拒绝、金额以服务端计划为准、用户间订单隔离。
"""

from datetime import datetime

from tests_server.base import ServerTestCase


class OrderTestCase(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.seed_plans()
        self.user, self.token = self.register_verified()
        # 预注册第二用户，用于跨用户隔离测试
        self.register_verified(email="other@example.com", username="other")
        self._other_token = self._login_token("other")

    def create_order(self, plan_code="monthly"):
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": plan_code})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def order_id(self, order_no):
        from server.app.database import SessionLocal
        from server.app.models import Order
        with SessionLocal() as db:
            return db.query(Order).filter(
                Order.order_no == order_no).one().id

    def create_admin(self, username="admin", email="admin@example.com"):
        user, token = super().create_admin(username=username, email=email)
        return user, token

    def _login_token(self, username):
        r = self.client.post("/api/auth/login", json={
            "identifier": username, "password": "Passw0rd!"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["access_token"]


class TestPlans(OrderTestCase):
    def test_plans_list_from_server(self):
        r = self.client.get("/api/plans")
        self.assertEqual(r.status_code, 200)
        plans = {p["plan_code"]: p for p in r.json()["plans"]}
        self.assertEqual(plans["monthly"]["price"], "29.00")
        self.assertEqual(plans["yearly"]["price"], "199.00")
        self.assertEqual(plans["monthly"]["currency"], "CNY")
        # 试用占位计划不对用户展示
        self.assertNotIn("trial", plans)


class TestOrderLifecycle(OrderTestCase):
    def test_create_order_reuses_pending(self):
        o1 = self.create_order()
        o2 = self.create_order()
        self.assertEqual(o1["order_no"], o2["order_no"])
        self.assertEqual(o2["amount"], "29.00")

    def test_unknown_plan_rejected(self):
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": "no-such-plan"})
        self.assertEqual(r.status_code, 400)

    def test_payment_proof_not_paid(self):
        """用户提交付款凭证只记录参考号，不等于已付款。"""
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        r = self.client.post(
            f"/api/orders/{oid}/payment-proof",
            headers=self.auth(self.token),
            json={"reference": "TXN-20260101-001"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["order_status"], "pending")
        self.assertEqual(r.json()["payment_reference"], "TXN-20260101-001")
        # 仍未开通订阅
        sub = self.client.get("/api/me/subscription",
                              headers=self.auth(self.token)).json()
        self.assertEqual(sub["status"], "trialing")

    def test_empty_reference_rejected(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        r = self.client.post(
            f"/api/orders/{oid}/payment-proof",
            headers=self.auth(self.token), json={"reference": "   "})
        self.assertEqual(r.status_code, 400)

    def test_order_isolation_between_users(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        r = self.client.get(f"/api/orders/{oid}",
                            headers=self.auth(self._other_token))
        self.assertEqual(r.status_code, 404)

    def test_list_orders_own_only(self):
        self.create_order()
        r = self.client.get("/api/orders", headers=self.auth(self.token))
        self.assertEqual(len(r.json()["orders"]), 1)
        r2 = self.client.get("/api/orders",
                             headers=self.auth(self._other_token))
        self.assertEqual(r2.json()["orders"], [])


class TestAdminConfirm(OrderTestCase):
    def test_non_admin_cannot_confirm(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(self.token),
            json={"approved": True, "reason": "自查"})
        self.assertEqual(r.status_code, 403)

    def test_confirm_payment_activates_subscription(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        _, admin_token = self.create_admin()
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token),
            json={"approved": True, "reason": "收款已核实"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["order_status"], "confirmed")
        # 订阅开通
        sub = self.client.get("/api/me/subscription",
                              headers=self.auth(self.token)).json()
        self.assertEqual(sub["status"], "active")
        self.assertTrue(sub["entitled"])
        self.assertEqual(sub["subscription"]["plan_code"], "monthly")
        # 用户收到通知邮件
        self.assertTrue(any("付款" in m["subject"] for m in self.mailer.sent))
        # 审计日志记录
        logs = self.client.get("/api/admin/audit-logs",
                               headers=self.auth(admin_token)).json()["logs"]
        self.assertTrue(any(l["action"] == "confirm_payment" for l in logs))

    def test_confirm_requires_reason(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        _, admin_token = self.create_admin()
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token),
            json={"approved": True, "reason": "  "})
        self.assertEqual(r.status_code, 400)

    def test_double_confirm_rejected(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        _, admin_token = self.create_admin()
        payload = {"approved": True, "reason": "收款已核实"}
        r1 = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token), json=payload)
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token), json=payload)
        self.assertEqual(r2.status_code, 409)
        self.assertIn("终态", r2.json()["detail"])

    def test_reject_payment_marks_failed(self):
        order = self.create_order()
        oid = self.order_id(order["order_no"])
        _, admin_token = self.create_admin()
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token),
            json={"approved": False, "reason": "凭证与收款不符"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["order_status"], "failed")
        sub = self.client.get("/api/me/subscription",
                              headers=self.auth(self.token)).json()
        self.assertEqual(sub["status"], "trialing")

    def test_yearly_order_activates_365_days(self):
        order = self.create_order(plan_code="yearly")
        oid = self.order_id(order["order_no"])
        _, admin_token = self.create_admin()
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(admin_token),
            json={"approved": True, "reason": "年费已核实"})
        self.assertEqual(r.status_code, 200)
        sub = self.client.get("/api/me/subscription",
                              headers=self.auth(self.token)).json()
        expires = sub["subscription"]["expires_at"]
        delta = (datetime.fromisoformat(expires.replace("Z", "+00:00"))
                 - datetime.now().astimezone()).days
        self.assertGreater(delta, 360)
        self.assertLessEqual(delta, 366)


class TestWebhook(OrderTestCase):
    def test_webhook_rejected_without_signature_key(self):
        from server.app.database import SessionLocal
        from server.services.orders import OrderError, process_webhook
        with SessionLocal() as db:
            with self.assertRaises(OrderError) as ctx:
                process_webhook(db, provider="alipay",
                                event_id="evt-001", signature="fake",
                                raw_body=b"{}")
        self.assertIn("签名密钥", str(ctx.exception))
