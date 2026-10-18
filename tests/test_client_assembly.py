"""客户端装配层单元测试（不依赖 PySide6，可在任何环境运行）。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class DataDirResolutionTests(unittest.TestCase):
    def test_dev_mode_uses_repo_data(self):
        from client.ui.context import default_data_dir
        # 非 frozen：应指向仓库内 data/
        self.assertEqual(default_data_dir(),
                         Path(__file__).resolve().parents[1] / "data")

    def test_frozen_mode_uses_exe_dir(self):
        from client.ui import context
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "CodeLabelStudio.exe"
            exe.write_text("", encoding="utf-8")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(
                    context.default_data_dir(),
                    (Path(tmp) / "data").resolve())

    def test_frozen_mode_reads_data_dir_txt(self):
        from client.ui import context
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "CodeLabelStudio.exe"
            exe.write_text("", encoding="utf-8")
            (Path(tmp) / "data_dir.txt").write_text(
                "D:/CustomData", encoding="utf-8")
            with mock.patch.object(sys, "frozen", True, create=True), \
                 mock.patch.object(sys, "executable", str(exe)):
                self.assertEqual(context.default_data_dir(),
                                 Path("D:/CustomData"))


class ApiClientUrlValidationTests(unittest.TestCase):
    def test_reject_non_http_schemes(self):
        from client.auth.api_client import ApiClient, ApiError
        for bad in ("file:///etc/passwd", "ftp://example.com",
                    "gopher://x", "not-a-url"):
            with self.assertRaises(ApiError):
                ApiClient(bad)

    def test_accept_http_https(self):
        from client.auth.api_client import ApiClient
        self.assertEqual(ApiClient("https://a.example.com").base_url,
                         "https://a.example.com")
        self.assertEqual(ApiClient("http://127.0.0.1:8000/").base_url,
                         "http://127.0.0.1:8000")

    def test_license_client_rejects_bad_scheme(self):
        from client.licensing.client_license import (
            LicenseApiClient, LicenseClientError,
        )
        with self.assertRaises(LicenseClientError):
            LicenseApiClient("file:///c:/secrets")


class DeviceFingerprintTests(unittest.TestCase):
    def test_fingerprint_stable_and_hashed(self):
        from client.auth.api_client import device_fingerprint
        fp1 = device_fingerprint()
        fp2 = device_fingerprint()
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 32)
        int(fp1, 16)  # 必须是十六进制


if __name__ == "__main__":
    unittest.main()
