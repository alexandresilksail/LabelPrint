"""安全原语单元测试：密码哈希 / 令牌 / 限流 / Ed25519 签名 / 离线宽限。

不经过 HTTP 层，直接验证安全属性本身。
"""

import os
import unittest

# 与集成测试一致：先设置环境再导入（引擎/密钥在导入时初始化）
_TMP = os.path.join(os.environ.get("TEMP", "/tmp"), "cls_sec_test")
os.makedirs(_TMP, exist_ok=True)
os.environ.setdefault("CLS_ENV", "development")
os.environ.setdefault("CLS_DATABASE_URL", f"sqlite:///{_TMP}/sec.sqlite3")
os.environ.setdefault("CLS_SECRET_KEY", "unit-test-secret")
os.environ.setdefault("CLS_LICENSE_PRIVATE_KEY", bytes(range(32)).hex())

from server.app.security import (  # noqa: E402
    RateLimiter, constant_time_eq, generate_token, hash_password, hash_token,
    needs_rehash, utc_now, verify_password,
)
from server.app.licensing import (  # noqa: E402
    canonical_payload, issue_credential, public_key_b64, sign_payload,
    verify_payload,
)
from server.services.license import offline_deadline  # noqa: E402


class TestPasswordHashing(unittest.TestCase):
    def test_hash_and_verify(self):
        h = hash_password("Passw0rd!")
        self.assertNotIn("Passw0rd!", h)
        self.assertTrue(h.startswith("$argon2id$"))
        self.assertTrue(verify_password("Passw0rd!", h))
        self.assertFalse(verify_password("wrong", h))

    def test_salt_randomized(self):
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        self.assertNotEqual(h1, h2)  # 每次随机盐

    def test_invalid_hash_returns_false(self):
        self.assertFalse(verify_password("x", "not-a-hash"))

    def test_needs_rehash_detects_bad_hash(self):
        self.assertTrue(needs_rehash("not-a-hash"))
        self.assertFalse(needs_rehash(hash_password("Passw0rd!")))


class TestTokens(unittest.TestCase):
    def test_token_uniqueness(self):
        tokens = {generate_token() for _ in range(100)}
        self.assertEqual(len(tokens), 100)

    def test_token_hash_deterministic(self):
        t = generate_token()
        self.assertEqual(hash_token(t), hash_token(t))
        self.assertNotEqual(hash_token(t), hash_token(t + "x"))

    def test_constant_time_eq(self):
        self.assertTrue(constant_time_eq("abc", "abc"))
        self.assertFalse(constant_time_eq("abc", "abd"))


class TestRateLimiter(unittest.TestCase):
    def test_allows_up_to_limit(self):
        rl = RateLimiter()
        results = [rl.check("k", 3) for _ in range(5)]
        self.assertEqual(results, [True, True, True, False, False])

    def test_window_expiry(self):
        rl = RateLimiter()
        self.assertTrue(rl.check("k", 1, window_seconds=0))
        # 窗口 0 秒：下一次即过期
        self.assertTrue(rl.check("k", 1, window_seconds=0))

    def test_reset_single_key(self):
        rl = RateLimiter()
        rl.check("a", 1)
        rl.check("b", 1)
        rl.reset("a")
        self.assertTrue(rl.check("a", 1))
        self.assertFalse(rl.check("b", 1))

    def test_reset_all(self):
        rl = RateLimiter()
        rl.check("a", 1)
        rl.check("b", 1)
        rl.reset()
        self.assertTrue(rl.check("a", 1))
        self.assertTrue(rl.check("b", 1))


class TestEd25519Signing(unittest.TestCase):
    def _cred(self):
        from datetime import timedelta
        return issue_credential(
            user_id=1, subscription_status="trialing", plan_code="trial",
            expires_at=utc_now() + timedelta(days=7), device_id=1)

    def test_sign_verify_roundtrip(self):
        cred = self._cred()
        pk = public_key_b64()
        self.assertTrue(
            verify_payload(pk, cred["payload"], cred["signature"]))

    def test_tampered_payload_rejected(self):
        cred = self._cred()
        payload = dict(cred["payload"])
        payload["subscription_status"] = "active"
        self.assertFalse(
            verify_payload(public_key_b64(), payload, cred["signature"]))

    def test_tampered_signature_rejected(self):
        cred = self._cred()
        sig = cred["signature"]
        flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
        self.assertFalse(
            verify_payload(public_key_b64(), cred["payload"], flipped))

    def test_wrong_key_rejected(self):
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from cryptography.hazmat.primitives import serialization
        other = Ed25519PrivateKey.generate()
        pk = base64.b64encode(other.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )).decode()
        cred = self._cred()
        self.assertFalse(
            verify_payload(pk, cred["payload"], cred["signature"]))

    def test_canonical_payload_stable(self):
        p1 = {"b": 1, "a": 2}
        p2 = {"a": 2, "b": 1}
        self.assertEqual(canonical_payload(p1), canonical_payload(p2))

    def test_sign_payload_deterministic(self):
        payload = {"x": 1}
        self.assertEqual(sign_payload(payload), sign_payload(payload))


class TestOfflineGrace(unittest.TestCase):
    def test_deadline_math(self):
        from datetime import timedelta
        now = utc_now()
        self.assertEqual(
            offline_deadline(now, grace_hours=72) - now, timedelta(hours=72))

    def test_deadline_default_from_settings(self):
        from server.app.config import settings
        from datetime import timedelta
        now = utc_now()
        self.assertEqual(
            offline_deadline(now) - now,
            timedelta(hours=settings.offline_grace_hours))


if __name__ == "__main__":
    unittest.main()
