"""端到端测试（TEST_PLAN.md D 节）：新用户全生命周期闭环。

链路：注册→邮箱验证（自动试用）→本地生成条码→四格式导出→试用期签发授权
→客户端离线验签→下单→管理员确认付款→订阅激活→重新签发→管理员撤销授权
→客户端联网验证被拒→本地数据保留。

本测试横跨服务端（TestClient）与客户端核心（纯 Python，无 GUI），
不使用 mock：服务端为真实应用实例，客户端条码/导出/验签为真实实现。
"""

import base64
import tempfile
import unittest
from pathlib import Path

from tests_server.base import ServerTestCase


class TestEndToEndUserJourney(ServerTestCase):
    """完整商业闭环：从注册到授权撤销。"""

    def setUp(self):
        super().setUp()
        self.seed_plans()
        # 步骤 1：注册并完成邮箱验证（基座 helper 内部完成验证调用）
        self.user, self.token = self.register_verified(
            username="e2e_user", email="e2e@example.com")
        self.admin_user, self.admin_token = self.create_admin(
            username="e2e_admin", email="e2e_admin@example.com")
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.out = Path(self.td.name)

    # ---------- 本地客户端辅助 ----------
    def make_local_service(self):
        from client.barcode.service import BarcodeService
        from client.database.db import Database
        db = Database(str(self.out / "codelabel.db"))
        self.addCleanup(db.close)
        return db, BarcodeService(db)

    def export_all_formats(self, svc):
        """步骤 4：导出 PDF / SVG / PNG / CSV 四种格式。"""
        from client.barcode.csvio import export_records_csv
        from client.barcode.renderer import render_png
        from client.barcode.svg import render_svg
        from client.printing.pdf import render_pdf, render_label_sheet_pdf
        from client.printing.templates import LabelTemplate
        records = svc.query_records()
        self.assertGreaterEqual(len(records), 3)

        # 单张 PDF
        render_pdf(records[0]["barcode_value"],
                   records[0]["barcode_type"],
                   str(self.out / "single.pdf"))
        self.assertTrue((self.out / "single.pdf").read_bytes()
                        .startswith(b"%PDF-"))

        # 标签版式 PDF（多页）
        items = [(r["barcode_value"], r["barcode_type"]) for r in records]
        template = LabelTemplate(
            template_name="E2E", paper_width=210, paper_height=297,
            label_width=60, label_height=40, rows=3, columns=3)
        meta = render_label_sheet_pdf(items, template,
                                      str(self.out / "sheet.pdf"))
        self.assertEqual(meta["labels_total"], len(items))
        self.assertGreaterEqual(meta["pages"], 1)

        # SVG
        svg, _ = render_svg(records[0]["barcode_value"],
                            records[0]["barcode_type"],
                            str(self.out / "barcode.svg"))
        self.assertIn("<svg", svg)

        # PNG
        render_png(records[0]["barcode_value"],
                   records[0]["barcode_type"],
                   str(self.out / "barcode.png"))
        self.assertTrue((self.out / "barcode.png").stat().st_size > 0)

        # CSV（含公式注入转义）
        export_records_csv(records, str(self.out / "records.csv"))
        csv_text = (self.out / "records.csv").read_text(encoding="utf-8-sig")
        self.assertIn("barcode_value", csv_text)

    # ---------- 步骤 2：试用开始 ----------
    def test_trial_started_after_verification(self):
        r = self.client.get("/api/me/subscription",
                            headers=self.auth(self.token))
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["subscription"]["status"], "trialing")
        self.assertIn("trial", body)
        self.assertEqual(body["trial"]["status"], "active")
        # 试用天数来自配置（默认 7 天）
        self.assertGreater(body["trial"]["days_remaining"], 0)

    # ---------- 步骤 3+4：本地生成与导出 ----------
    def test_local_generation_and_export(self):
        _, svc = self.make_local_service()
        rec1 = svc.generate_single("UPC-A",
                                   explicit_value="03600029145")
        self.assertEqual(rec1["barcode_value"], "036000291452")
        rec2 = svc.generate_single("EAN-13",
                                   explicit_value="5901234123457")
        self.assertEqual(rec2["barcode_value"], "5901234123457")
        rec3 = svc.generate_single("EAN-8", explicit_value="96385074")
        self.assertEqual(rec3["barcode_value"], "96385074")
        self.export_all_formats(svc)

    # ---------- 步骤 5+6：试用期授权 + 客户端离线验签 ----------
    def test_trial_license_issue_and_offline_verify(self):
        fp = "e2e-fingerprint-0001"
        r = self.client.post("/api/license/activate-device",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "E2E机"})
        self.assertEqual(r.status_code, 200, r.text)

        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "E2E机"})
        self.assertEqual(r.status_code, 200, r.text)
        credential = r.json()["credential"]

        # 客户端侧：用服务端公钥离线验签
        pk = self.client.get("/api/license/public-key").json()
        from client.licensing.client_license import (
            CachedCredential, offline_status, verify_credential)
        self.assertTrue(verify_credential(credential, pk["public_key_b64"]))
        cached = CachedCredential(credential=credential,
                                  public_key_b64=pk["public_key_b64"],
                                  last_validated_at=r.json()["server_time"])
        status = offline_status(cached)
        self.assertTrue(status["entitled"])
        self.assertEqual(status["reason"], "ok")
        self.assertEqual(credential["payload"]["subscription_status"],
                         "trialing")

    # ---------- 步骤 7+8+9：下单 → 管理员确认 → 订阅激活 ----------
    def test_purchase_flow_activates_subscription(self):
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": "monthly"})
        self.assertEqual(r.status_code, 201, r.text)
        order_id = r.json()["id"]

        r = self.client.post(f"/api/admin/orders/{order_id}/confirm-payment",
                             headers=self.auth(self.admin_token),
                             json={"approved": True, "reason": "E2E 测试确认"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["order_status"], "confirmed")

        r = self.client.get("/api/me/subscription",
                            headers=self.auth(self.token))
        sub = r.json()["subscription"]
        self.assertEqual(sub["status"], "active")
        self.assertEqual(sub["plan_code"], "monthly")

        # 付费后可重新签发授权，载荷带计划编码
        fp = "e2e-fingerprint-0002"
        self.client.post("/api/license/activate-device",
                         headers=self.auth(self.token),
                         json={"fingerprint": fp, "device_name": "E2E机"})
        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "E2E机"})
        self.assertEqual(r.status_code, 200, r.text)
        credential = r.json()["credential"]
        self.assertEqual(credential["payload"]["plan_code"], "monthly")
        self.assertEqual(credential["payload"]["subscription_status"],
                         "active")

    # ---------- 步骤 10+11：撤销授权 → 客户端验证被拒 ----------
    def test_revoke_license_blocks_client(self):
        fp = "e2e-fingerprint-0003"
        self.client.post("/api/license/activate-device",
                         headers=self.auth(self.token),
                         json={"fingerprint": fp, "device_name": "E2E机"})
        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "E2E机"})
        credential = r.json()["credential"]
        license_id = credential["license_id"]

        # 撤销前：联网验证通过
        r = self.client.post("/api/license/validate",
                             headers=self.auth(self.token),
                             json={"credential": credential})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["valid"])

        # 管理员撤销
        r = self.client.post(f"/api/admin/licenses/{license_id}/revoke",
                             headers=self.auth(self.admin_token),
                             json={"reason": "E2E 测试撤销"})
        self.assertEqual(r.status_code, 200, r.text)

        # 撤销后：联网验证被拒
        r = self.client.post("/api/license/validate",
                             headers=self.auth(self.token),
                             json={"credential": credential})
        self.assertIn(r.status_code, (400, 403))

        # 客户端离线状态：标记 revoked 后不可用
        pk = self.client.get("/api/license/public-key").json()
        from client.licensing.client_license import (
            CachedCredential, offline_status, verify_credential)
        cached = CachedCredential(credential=credential,
                                  public_key_b64=pk["public_key_b64"],
                                  last_validated_at=r.json().get(
                                      "server_time", ""),
                                  revoked=True)
        status = offline_status(cached)
        self.assertFalse(status["entitled"])
        self.assertEqual(status["reason"], "revoked")

    # ---------- 步骤 12：本地数据保留（重开数据库） ----------
    def test_local_data_survives_reopen(self):
        from client.barcode.service import BarcodeService
        from client.database.db import Database
        db_path = str(self.out / "reopen.db")
        db = Database(db_path)
        svc = BarcodeService(db)
        svc.generate_single("UPC-A", explicit_value="03600029145")
        svc.generate_single("EAN-13", explicit_value="5901234123457")
        db.close()

        db2 = Database(db_path)
        self.addCleanup(db2.close)
        svc2 = BarcodeService(db2)
        records = svc2.query_records()
        self.assertEqual(len(records), 2)
        values = {r["barcode_value"] for r in records}
        self.assertEqual(values, {"036000291452", "5901234123457"})
        # 重复生成仍被拒（约束保留）
        with self.assertRaises(Exception):
            svc2.generate_single("UPC-A", explicit_value="03600029145")

    # ---------- 全链路串联（一次跑完 D 节主路径） ----------
    def test_full_journey_single_run(self):
        # 1-2. 注册+验证+试用（setUp 已完成，断言试用态）
        r = self.client.get("/api/me/subscription",
                            headers=self.auth(self.token))
        self.assertEqual(r.json()["subscription"]["status"], "trialing")

        # 3-4. 本地生成 + 四格式导出
        _, svc = self.make_local_service()
        for i in range(5):
            base = f"5901234123{i:02d}"[:12]
            from client.barcode.checkdigit import calculate_check_digit
            value = base + calculate_check_digit(base, "EAN-13")
            svc.generate_single("EAN-13", explicit_value=value)
        self.export_all_formats(svc)

        # 5-6. 试用期授权 + 离线验签
        fp = "e2e-journey-fp"
        self.client.post("/api/license/activate-device",
                         headers=self.auth(self.token),
                         json={"fingerprint": fp, "device_name": "旅程机"})
        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "旅程机"})
        trial_credential = r.json()["credential"]
        pk = self.client.get("/api/license/public-key").json()
        from client.licensing.client_license import (
            CachedCredential, offline_status, verify_credential)
        self.assertTrue(verify_credential(trial_credential,
                                          pk["public_key_b64"]))

        # 7-9. 下单 → 确认 → 激活 → 重新签发
        r = self.client.post("/api/orders", headers=self.auth(self.token),
                             json={"plan_code": "yearly"})
        order_id = r.json()["id"]
        self.client.post(f"/api/admin/orders/{order_id}/confirm-payment",
                         headers=self.auth(self.admin_token),
                         json={"approved": True, "reason": "E2E 旅程确认"})
        r = self.client.get("/api/me/subscription",
                            headers=self.auth(self.token))
        self.assertEqual(r.json()["subscription"]["status"], "active")
        r = self.client.post("/api/license/issue",
                             headers=self.auth(self.token),
                             json={"fingerprint": fp, "device_name": "旅程机"})
        paid_credential = r.json()["credential"]
        self.assertEqual(paid_credential["payload"]["plan_code"], "yearly")

        # 10-11. 撤销 → 客户端验证被拒
        license_id = paid_credential["license_id"]
        self.client.post(f"/api/admin/licenses/{license_id}/revoke",
                         headers=self.auth(self.admin_token),
                         json={"reason": "E2E 旅程撤销"})
        r = self.client.post("/api/license/validate",
                             headers=self.auth(self.token),
                             json={"credential": paid_credential})
        self.assertIn(r.status_code, (400, 403))
        revoked_cache = CachedCredential(
            credential=paid_credential,
            public_key_b64=pk["public_key_b64"], revoked=True)
        self.assertFalse(offline_status(revoked_cache)["entitled"])

        # 12. 本地数据保留
        self.assertEqual(len(svc.query_records()), 5)


if __name__ == "__main__":
    unittest.main()
