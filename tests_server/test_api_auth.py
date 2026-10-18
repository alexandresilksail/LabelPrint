"""认证 API 集成测试：注册/验证/登录/登出/改密/重置。

覆盖：正常流程、参数校验、防枚举、幂等、限流、会话撤销。
"""

from tests_server.base import ServerTestCase


class TestRegister(ServerTestCase):
    def test_register_requires_terms(self):
        r = self.register(accept_terms=False)
        self.assertEqual(r.status_code, 422)
        self.assertIn("用户协议", r.text)

    def test_register_validates_password(self):
        r = self.register(password="short")
        self.assertEqual(r.status_code, 400)
        self.assertIn("8", r.json()["detail"])

    def test_register_validates_username(self):
        r = self.register(username="a b")
        self.assertEqual(r.status_code, 400)

    def test_register_success_pending(self):
        r = self.register()
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertFalse(body["email_verified"])
        # 注册后发出验证邮件
        self.assertTrue(any("邮箱验证" in m["subject"] for m in self.mailer.sent))

    def test_register_duplicate_unified_message(self):
        self.register(email="dup@example.com", username="dupuser")
        # 同邮箱不同用户名 —— 统一文案，不暴露是邮箱还是用户名重复
        r = self.register(email="dup@example.com", username="other")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"], "该邮箱或用户名已被注册")


class TestEmailVerification(ServerTestCase):
    def test_verify_email_starts_trial(self):
        r = self.register()
        self.assertEqual(r.status_code, 201)
        token = self.mailer.token_for("verify-email")
        r = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["verified"])
        self.assertIn("trial_expires_at", body)
        # 验证后账号可登录
        user, token = self.login("user1")
        self.assertEqual(user["email"], "u1@example.com")

    def test_verify_token_single_use(self):
        self.register()
        token = self.mailer.token_for("verify-email")
        r1 = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(r1.status_code, 200)
        r2 = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(r2.status_code, 400)
        self.assertIn("无效或已过期", r2.json()["detail"])

    def test_verify_bad_token(self):
        r = self.client.post("/api/auth/verify-email",
                             json={"token": "not-a-real-token"})
        self.assertEqual(r.status_code, 400)

    def test_trial_idempotent(self):
        """verify-email 重复调用不会创建第二个试用（幂等）。"""
        self.register()
        token = self.mailer.token_for("verify-email")
        r = self.client.post("/api/auth/verify-email", json={"token": token})
        self.assertEqual(r.status_code, 200, r.text)
        first_expiry = r.json()["trial_expires_at"]
        # 直接调服务层再启一次，应返回同一记录
        from server.app.database import SessionLocal
        from server.app.models import Trial, User
        from server.services.trial import start_trial
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == "user1").one()
            trial = start_trial(db, user)
            self.assertEqual(trial.expires_at.isoformat() + "Z", first_expiry)
            count = db.query(Trial).count()
        self.assertEqual(count, 1)


class TestLogin(ServerTestCase):
    def test_login_before_verify_rejected(self):
        self.register()
        r = self.client.post("/api/auth/login", json={
            "identifier": "user1", "password": "Passw0rd!"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("尚未激活", r.json()["detail"])

    def test_login_wrong_password_unified_error(self):
        self.register_verified()
        r = self.client.post("/api/auth/login", json={
            "identifier": "user1", "password": "WrongPassw0rd!"})
        self.assertEqual(r.status_code, 401)
        # 不存在用户同样文案（防枚举）
        r2 = self.client.post("/api/auth/login", json={
            "identifier": "nobody", "password": "WrongPassw0rd!"})
        self.assertEqual(r2.status_code, 401)
        self.assertEqual(r.json()["detail"], r2.json()["detail"])

    def test_login_by_email_or_username(self):
        self.register_verified()
        _, t1 = self.login("user1")
        _, t2 = self.login("u1@example.com")
        self.assertTrue(t1 and t2)

    def test_logout_revokes_session(self):
        self.register_verified()
        user, token = self.login("user1")
        r = self.client.post("/api/auth/logout", headers=self.auth(token))
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(r.status_code, 401)


class TestPasswordFlows(ServerTestCase):
    def test_change_password(self):
        self.register_verified()
        _, token = self.login("user1")
        r = self.client.post("/api/auth/change-password",
                             headers=self.auth(token),
                             json={"old_password": "Passw0rd!",
                                   "new_password": "NewPassw0rd!"})
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.post("/api/auth/login", json={
            "identifier": "user1", "password": "NewPassw0rd!"})
        self.assertEqual(r.status_code, 200)

    def test_change_password_wrong_old(self):
        self.register_verified()
        _, token = self.login("user1")
        r = self.client.post("/api/auth/change-password",
                             headers=self.auth(token),
                             json={"old_password": "wrong",
                                   "new_password": "NewPassw0rd!"})
        self.assertEqual(r.status_code, 401)

    def test_reset_password_revokes_sessions(self):
        self.register_verified()
        _, token = self.login("user1")
        # 忘记密码（防枚举：不存在邮箱也返回成功）
        r = self.client.post("/api/auth/forgot-password",
                             json={"email": "nobody@example.com"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/auth/forgot-password",
                             json={"email": "u1@example.com"})
        self.assertEqual(r.status_code, 200)
        reset_token = self.mailer.token_for("reset-password")
        r = self.client.post("/api/auth/reset-password", json={
            "token": reset_token, "new_password": "ResetPassw0rd!"})
        self.assertEqual(r.status_code, 200, r.text)
        # 旧会话全部撤销
        r = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(r.status_code, 401)
        # 新密码可登录
        r = self.client.post("/api/auth/login", json={
            "identifier": "user1", "password": "ResetPassw0rd!"})
        self.assertEqual(r.status_code, 200)

    def test_reset_token_single_use(self):
        self.register_verified()
        self.client.post("/api/auth/forgot-password",
                         json={"email": "u1@example.com"})
        reset_token = self.mailer.token_for("reset-password")
        r = self.client.post("/api/auth/reset-password", json={
            "token": reset_token, "new_password": "ResetPassw0rd!"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post("/api/auth/reset-password", json={
            "token": reset_token, "new_password": "AnotherPassw0rd!"})
        self.assertEqual(r.status_code, 400)


class TestAccessControl(ServerTestCase):
    def test_me_requires_auth(self):
        r = self.client.get("/api/me")
        self.assertEqual(r.status_code, 401)

    def test_me_returns_profile(self):
        self.register_verified()
        user, token = self.login("user1")
        r = self.client.get("/api/me", headers=self.auth(token))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["username"], "user1")
        self.assertTrue(body["email_verified"])
        self.assertFalse(body["is_admin"])
        self.assertIn("server_time_utc", body)

    def test_invalid_token_rejected(self):
        r = self.client.get("/api/me",
                            headers=self.auth("forged-token-value"))
        self.assertEqual(r.status_code, 401)


class TestRateLimit(ServerTestCase):
    def test_register_rate_limited(self):
        from server.app.config import settings
        limit = settings.rate_limit_register
        statuses = []
        for i in range(limit + 2):
            r = self.register(email=f"rl{i}@example.com",
                              username=f"rluser{i}")
            statuses.append(r.status_code)
        self.assertIn(429, statuses)
        # 超限返回 429 且前 limit 个成功
        self.assertEqual(statuses[:limit], [201] * limit)
