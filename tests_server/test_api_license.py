"""授权 API 集成测试：设备激活 / 授权签发 / 联网验证 / 撤销 / 离线宽限。

覆盖：正常签发链路、设备数量上限、设备解绑连带撤销、
篡改载荷验签失败、撤销后验证拒绝、试用状态门禁。
"""

import base64

from tests_server.base import ServerTestCase


class LicenseFlowTestCase(ServerTestCase):
    """注册+验证+登录的已验证用户 + 已激活设备 + 授权凭证。"""

    def setUp(self):
        super().setUp()
        self.user, self.token = self.register_verified()
        self.fp = "fp-abcdef123456"

    def activate(self, fingerprint=None, name="测试机"):
        r = self.client.post(
            "/api/license/activate-device", headers=self.auth(self.token),
            json={"fingerprint": fingerprint or self.fp,
                  "device_name": name})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def issue(self, fingerprint=None):
        r = self.client.post(
            "/api/license/issue", headers=self.auth(self.token),
            json={"fingerprint": fingerprint or self.fp,
                  "device_name": "测试机"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestDeviceActivation(LicenseFlowTestCase):
    def test_activate_and_reuse(self):
        d1 = self.activate()
        self.assertEqual(d1["status"], "active")
        # 同一指纹重复激活 = 复用，不新增
        d2 = self.activate()
        self.assertEqual(d1["device_id"], d2["device_id"])
        r = self.client.get("/api/me/devices", headers=self.auth(self.token))
        self.assertEqual(len(r.json()["devices"]), 1)

    def test_activate_rejects_bad_fingerprint(self):
        r = self.client.post(
            "/api/license/activate-device", headers=self.auth(self.token),
            json={"fingerprint": "short"})
        self.assertEqual(r.status_code, 400)

    def test_device_limit(self):
        from server.app.config import settings
        for i in range(settings.max_devices):
            self.activate(fingerprint=f"fp-device-{i}")
        r = self.client.post(
            "/api/license/activate-device", headers=self.auth(self.token),
            json={"fingerprint": "fp-one-too-many"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("上限", r.json()["detail"])

    def test_revoke_device_revokes_license(self):
        self.issue()
        devices = self.client.get(
            "/api/me/devices", headers=self.auth(self.token)).json()["devices"]
        device_id = devices[0]["id"]
        r = self.client.post(
            "/api/me/devices/revoke", headers=self.auth(self.token),
            json={"device_id": device_id})
        self.assertEqual(r.status_code, 200)
        # 设备已撤销，再次激活被拒
        r = self.client.post(
            "/api/license/activate-device", headers=self.auth(self.token),
            json={"fingerprint": self.fp})
        self.assertEqual(r.status_code, 400)
        self.assertIn("解绑", r.json()["detail"])


class TestLicenseIssueAndValidate(LicenseFlowTestCase):
    def test_issue_returns_signed_credential(self):
        result = self.issue()
        cred = result["credential"]
        self.assertIn("payload", cred)
        self.assertIn("signature", cred)
        self.assertEqual(cred["payload"]["user_id"], self.user["id"])
        self.assertEqual(result["offline_grace_hours"], 72)
        # 签名可用服务端公钥验证
        from server.app.licensing import verify_payload
        pk = self.client.get("/api/license/public-key").json()["public_key_b64"]
        self.assertTrue(
            verify_payload(pk, cred["payload"], cred["signature"]))

    def test_validate_ok(self):
        result = self.issue()
        r = self.client.post(
            "/api/license/validate", headers=self.auth(self.token),
            json={"credential": result["credential"]})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["valid"])
        self.assertEqual(body["subscription_status"], "trialing")

    def test_validate_tampered_payload_rejected(self):
        result = self.issue()
        cred = dict(result["credential"])
        payload = dict(cred["payload"])
        payload["subscription_status"] = "active"  # 篡改状态
        cred["payload"] = payload
        r = self.client.post(
            "/api/license/validate", headers=self.auth(self.token),
            json={"credential": cred})
        self.assertEqual(r.status_code, 400)

    def test_validate_unknown_license(self):
        r = self.client.post(
            "/api/license/validate", headers=self.auth(self.token),
            json={"credential": {"payload": {"license_id": "no-such-id"}}})
        self.assertEqual(r.status_code, 400)

    def test_validate_device_mismatch(self):
        """授权记录被重绑到其他设备后，原凭证（签名有效）验证拒绝。"""
        self.activate(fingerprint="fp-device-a")
        self.activate(fingerprint="fp-device-b")
        result = self.issue(fingerprint="fp-device-a")
        # 在库内把授权改绑到另一台设备（模拟重绑/迁移场景）
        from server.app.database import SessionLocal
        from server.app.models import License
        with SessionLocal() as db:
            lic = db.query(License).filter(
                License.license_id ==
                result["credential"]["payload"]["license_id"]).one()
            lic.device_id = lic.device.id + 1
            db.commit()
        r = self.client.post(
            "/api/license/validate", headers=self.auth(self.token),
            json={"credential": result["credential"]})
        self.assertEqual(r.status_code, 400)
        self.assertIn("设备", r.json()["detail"])

    def test_public_key_endpoint(self):
        r = self.client.get("/api/license/public-key")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["algorithm"], "Ed25519")
        raw = base64.b64decode(r.json()["public_key_b64"])
        self.assertEqual(len(raw), 32)


class TestLicenseStateGating(LicenseFlowTestCase):
    def test_issue_requires_entitled_status(self):
        """未验证邮箱（pending）用户无法使用任何授权接口。

        会话层对非 active 账号一律 401（会话视为无效）。
        """
        r = self.register(email="u2@example.com", username="user2")
        self.assertEqual(r.status_code, 201)
        _, token2 = self._login_pending("user2")
        r = self.client.post(
            "/api/license/issue", headers=self.auth(token2),
            json={"fingerprint": "fp-u2"})
        self.assertEqual(r.status_code, 401)

    def _login_pending(self, username):
        from server.app.database import SessionLocal
        from server.app.models import User
        from server.app.security import generate_token, hash_token, utc_now
        from datetime import timedelta
        from server.app.models import Session as SessionModel
        with SessionLocal() as db:
            user = db.query(User).filter(User.username == username).one()
            token = generate_token()
            db.add(SessionModel(
                user_id=user.id, token_hash=hash_token(token),
                expires_at=utc_now() + timedelta(hours=72)))
            db.commit()
        return user, token

    def test_offline_deadline_helper(self):
        from datetime import timedelta
        from server.app.security import utc_now
        from server.services.license import offline_deadline
        now = utc_now()
        deadline = offline_deadline(now, grace_hours=72)
        self.assertEqual(deadline - now, timedelta(hours=72))
