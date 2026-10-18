"""管理后台 API 集成测试：用户管理 / 停用恢复 / 试用延长 / 统计 / 审计。

覆盖：管理员全局鉴权（依赖级）、非管理员 403、停用后会话失效、
试用延长幂等边界、统计数字自洽、审计留痕。
"""

from datetime import datetime, timedelta

from tests_server.base import ServerTestCase


class AdminTestCase(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.admin, self.admin_token = self.create_admin()
        self.user, self.token = self.register_verified()

    def test_admin_endpoints_require_admin(self):
        """全部 /api/admin/* 对非管理员 403（router 级依赖）。"""
        paths = [
            ("GET", "/api/admin/users"),
            ("GET", "/api/admin/orders"),
            ("GET", "/api/admin/stats"),
            ("GET", "/api/admin/audit-logs"),
        ]
        for method, path in paths:
            r = self.client.request(method, path,
                                    headers=self.auth(self.token))
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    def test_admin_endpoints_require_auth(self):
        r = self.client.get("/api/admin/users")
        self.assertEqual(r.status_code, 401)

    def test_user_list_and_search(self):
        r = self.client.get("/api/admin/users",
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 2)  # admin + user1
        self.assertEqual(len(body["users"]), 2)
        r = self.client.get("/api/admin/users", params={"q": "user1"},
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.json()["total"], 1)

    def test_user_detail_includes_trial(self):
        uid = self.user["id"]
        r = self.client.get(f"/api/admin/users/{uid}",
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["user"]["username"], "user1")
        self.assertIsNotNone(body["trial"])
        self.assertEqual(body["trial"]["status"], "active")
        self.assertEqual(body["effective_status"], "trialing")

    def test_suspend_invalidates_session(self):
        uid = self.user["id"]
        r = self.client.post(
            f"/api/admin/users/{uid}/suspend", headers=self.auth(self.admin_token),
            json={"reason": "违规使用"})
        self.assertEqual(r.status_code, 200, r.text)
        # 停用后既有会话立即失效
        r = self.client.get("/api/me", headers=self.auth(self.token))
        self.assertEqual(r.status_code, 401)
        # 审计留痕
        logs = self.client.get("/api/admin/audit-logs",
                               headers=self.auth(self.admin_token)).json()["logs"]
        self.assertTrue(any(l["action"] == "suspend_user" for l in logs))

    def test_suspend_requires_reason(self):
        uid = self.user["id"]
        r = self.client.post(
            f"/api/admin/users/{uid}/suspend", headers=self.auth(self.admin_token),
            json={"reason": ""})
        self.assertEqual(r.status_code, 400)

    def test_resume_user(self):
        uid = self.user["id"]
        self.client.post(f"/api/admin/users/{uid}/suspend",
                         headers=self.auth(self.admin_token),
                         json={"reason": "违规使用"})
        r = self.client.post(f"/api/admin/users/{uid}/resume",
                             headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        # 停用前签发的会话不会自动恢复（令牌仍在，但需重新登录）
        r = self.client.post("/api/auth/login", json={
            "identifier": "user1", "password": "Passw0rd!"})
        self.assertEqual(r.status_code, 200)

    def test_extend_trial(self):
        uid = self.user["id"]
        before = self.client.get(
            f"/api/admin/users/{uid}",
            headers=self.auth(self.admin_token)).json()["trial"]["expires_at"]
        r = self.client.post(
            f"/api/admin/users/{uid}/extend-trial",
            headers=self.auth(self.admin_token),
            json={"days": 7, "reason": "用户反馈需要更多时间评估"})
        self.assertEqual(r.status_code, 200, r.text)
        after = datetime.fromisoformat(r.json()["expires_at"].replace("Z", "+00:00"))
        before_dt = datetime.fromisoformat(before.replace("Z", "+00:00"))
        self.assertEqual(after - before_dt, timedelta(days=7))
        # 延长必须填原因
        r = self.client.post(
            f"/api/admin/users/{uid}/extend-trial",
            headers=self.auth(self.admin_token), json={"days": 7, "reason": ""})
        self.assertEqual(r.status_code, 400)
        # 天数边界
        r = self.client.post(
            f"/api/admin/users/{uid}/extend-trial",
            headers=self.auth(self.admin_token),
            json={"days": 0, "reason": "测试"})
        self.assertEqual(r.status_code, 400)

    def test_extend_trial_blocked_with_active_subscription(self):
        """已有有效订阅的用户不再延长试用。"""
        self.seed_plans()
        order = self.client.post("/api/orders", headers=self.auth(self.token),
                                 json={"plan_code": "monthly"})
        self.assertEqual(order.status_code, 201)
        from server.app.database import SessionLocal
        from server.app.models import Order
        with SessionLocal() as db:
            oid = db.query(Order).filter(
                Order.user_id == self.user["id"]).one().id
        r = self.client.post(
            f"/api/admin/orders/{oid}/confirm-payment",
            headers=self.auth(self.admin_token),
            json={"approved": True, "reason": "收款已核实"})
        self.assertEqual(r.status_code, 200)
        uid = self.user["id"]
        r = self.client.post(
            f"/api/admin/users/{uid}/extend-trial",
            headers=self.auth(self.admin_token),
            json={"days": 7, "reason": "无效延长"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("订阅", r.json()["detail"])

    def test_stats_self_consistent(self):
        r = self.client.get("/api/admin/stats",
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        stats = r.json()
        self.assertEqual(stats["users_total"], 2)
        # 仅 user1 验证过邮箱（seed 管理员未验证）
        self.assertEqual(stats["email_verified"], 1)
        self.assertEqual(stats["trialing"], 1)
        self.assertEqual(stats["active_subscriptions"], 0)
        self.assertEqual(stats["confirmed_revenue"], "0.00")
        self.assertIn("generated_at", stats)

    def test_pagination_bounds(self):
        """非法分页参数被钳制而非报错。"""
        r = self.client.get("/api/admin/users",
                            params={"page": -5, "size": 9999},
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["page"], 1)
        self.assertLessEqual(body["size"], 100)


class TestSubscriptionAdjust(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.seed_plans()
        self.admin, self.admin_token = self.create_admin()
        self.user, self.token = self.register_verified()

    def _subscription_id(self):
        from server.app.database import SessionLocal
        from server.app.models import Subscription
        with SessionLocal() as db:
            sub = db.query(Subscription).filter(
                Subscription.user_id == self.user["id"]).one()
            return sub.id

    def test_adjust_requires_reason(self):
        sid = self._subscription_id()
        r = self.client.post(
            f"/api/admin/subscriptions/{sid}/adjust",
            headers=self.auth(self.admin_token),
            json={"action": "suspend", "reason": ""})
        self.assertEqual(r.status_code, 400)

    def test_suspend_and_resume_subscription(self):
        sid = self._subscription_id()
        r = self.client.post(
            f"/api/admin/subscriptions/{sid}/adjust",
            headers=self.auth(self.admin_token),
            json={"action": "suspend", "reason": "疑似共享账号"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "suspended")
        r = self.client.get("/api/license/status",
                            headers=self.auth(self.token))
        self.assertEqual(r.json()["status"], "suspended")
        # 暂停状态不能签发授权
        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": "fp-suspended-user"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post(
            f"/api/admin/subscriptions/{sid}/adjust",
            headers=self.auth(self.admin_token),
            json={"action": "resume", "reason": "核实无误"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "trialing")

    def test_adjust_unknown_action(self):
        sid = self._subscription_id()
        r = self.client.post(
            f"/api/admin/subscriptions/{sid}/adjust",
            headers=self.auth(self.admin_token),
            json={"action": "teleport", "reason": "测试"})
        self.assertEqual(r.status_code, 400)

    # ---------- 管理后台前端对接 ----------
    def test_admin_orders_list_includes_id_and_username(self):
        """管理后台「确认付款」按钮依赖订单 id 与用户名展示。"""
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": "monthly"})
        self.assertEqual(r.status_code, 201, r.text)
        r = self.client.get("/api/admin/orders",
                            headers=self.auth(self.admin_token))
        self.assertEqual(r.status_code, 200)
        orders = r.json()["orders"]
        self.assertGreaterEqual(len(orders), 1)
        order = orders[0]
        self.assertIn("id", order)
        self.assertIsInstance(order["id"], int)
        self.assertEqual(order["username"], self.user["username"])
        self.assertIn("payment_reference", order)

    def test_admin_console_static_mounted(self):
        """/admin 提供管理后台 SPA（开发模式挂载）。"""
        r = self.client.get("/admin/")
        if r.status_code == 404:
            self.skipTest("admin 静态目录未随包分发")
        self.assertEqual(r.status_code, 200)
        self.assertIn("CodeLabelStudio", r.text)
        for asset in ("/admin/app.js", "/admin/style.css"):
            r = self.client.get(asset)
            self.assertEqual(r.status_code, 200, asset)

    def test_confirm_payment_writes_audit_log(self):
        """确认付款后审计日志留痕（管理后台审计页数据源）。"""
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": "monthly"})
        order = r.json()
        r = self.client.post(
            f"/api/admin/orders/{order['id']}/confirm-payment",
            headers=self.auth(self.admin_token),
            json={"approved": True, "reason": "已核对到账"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get("/api/admin/audit-logs",
                            headers=self.auth(self.admin_token))
        actions = [l["action"] for l in r.json()["logs"]]
        self.assertIn("confirm_payment", actions)
