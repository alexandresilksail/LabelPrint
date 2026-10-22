"""第四轮 Authenticode 吊销/链策略、HRESULT 有符号归一化、签名者提取、
结果新鲜度（失配重验）与 skip 隔离的纯逻辑测试（Linux 全量可跑）。

诚信红线覆盖：
1. HRESULT：ctypes restype=c_long 返回有符号负值，必须先 & 0xFFFFFFFF 归一化；
   有符号负值绝不误判为成功。
2. 吊销/链策略矩阵：明确吊销→invalid（阻止）；未签名→unsigned（阻止）；
   链不可信/链错误→invalid（阻止）；吊销未知/服务器离线→not_verifiable（阻止）；
   用户取消/系统拒绝→not_verifiable（阻止）；全绿→ok（放行）。
3. signature_stale：同路径未变→False；改 mtime/size→True；删除→True；
   None→True；换路径→True；缺快照→True。
4. skip 隔离：生产 UI（print_export.py）源码不得出现 skip_signature_check=True；
   elevate_process(skip_signature_check=True) 在 Linux 仍被平台守卫抛
   UNSUPPORTED_PLATFORM，守卫不被绕过。
5. 签名者：注入 fake provider 模拟 CryptQueryObject → 提取成功得显示名；
   提取失败/非 ok → publisher=None；UI 文案“发布者：未知”，不猜测。
6. Windows 分支源码断言：WTD_STATEACTION_CLOSE / dwStateAction / cbStruct /
   WTD_REVOKE_WHOLECHAIN / CertFreeCertificateContext 等真实实现字面量存在，
   保证不是占位。

Windows 真机 WinVerifyTrust 结果待 Windows 执行验证；本文件不伪造任何真机调用。
"""

from __future__ import annotations

import pathlib

import pytest

from client.printing.authenticode import (
    CERT_E_CHAINING,
    CERT_E_EXPIRED,
    CERT_E_REVOKED,
    CERT_E_REVOCATION_FAILURE,
    CERT_E_UNTRUSTEDROOT,
    CRYPT_E_REVOCATION_OFFLINE,
    ERROR_ACCESS_DENIED,
    ERROR_CANCELLED,
    TRUST_E_EXPLICIT_DISTRUST,
    TRUST_E_NOSIGNATURE,
    TRUST_E_BAD_DIGEST,
    VerificationStatus,
    SignatureStatus,
    _extract_signer_info,
    classify_winverifytrust,
    describe_signature_status,
    file_snapshot,
    normalize_hresult,
    should_block_elevation,
    signature_stale,
    verify_authenticode,
)
from client.printing.driver_installer import elevate_process
from client.printing.errors import PrintError

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_AUTHENTICODE_PY = _REPO_ROOT / "client" / "printing" / "authenticode.py"
_PRINT_EXPORT_PY = _REPO_ROOT / "client" / "ui" / "pages" / "print_export.py"


def _status(verification: VerificationStatus, **kw) -> SignatureStatus:
    base = dict(path="C:\\fake\\file.exe", file_exists=True,
                verification=verification, failure_reason=None)
    base.update(kw)
    return SignatureStatus(**base)


# ---------------------------------------------------------------------------
# HRESULT 归一化：有符号 c_long 负值 → 无符号，且不误判成功
# ---------------------------------------------------------------------------
def _signed(unsigned: int) -> int:
    """模拟 ctypes c_long 把高位 HRESULT 解释成的有符号 32 位负值。"""
    return unsigned - (1 << 32)


@pytest.mark.parametrize("unsigned", [
    TRUST_E_NOSIGNATURE, CERT_E_REVOKED, CERT_E_CHAINING,
    CERT_E_REVOCATION_FAILURE, CRYPT_E_REVOCATION_OFFLINE,
])
def test_normalize_hresult_signed_negative_matches_unsigned(unsigned):
    signed = _signed(unsigned)
    assert signed < 0  # 确实是 ctypes 有符号负值
    assert normalize_hresult(signed) == unsigned
    assert normalize_hresult(unsigned) == unsigned


def test_zero_is_only_success_anything_else_blocked():
    v, reason = classify_winverifytrust(0)
    assert v is VerificationStatus.OK
    assert reason is None
    # 正的小整数（非 0）绝不被当作成功
    v2, _ = classify_winverifytrust(1)
    assert v2 is not VerificationStatus.OK


# ---------------------------------------------------------------------------
# HRESULT → 分类参数化（含有符号负值与无符号两种传入）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hr,expected", [
    (0, VerificationStatus.OK),
    # 无符号传入
    (TRUST_E_NOSIGNATURE, VerificationStatus.UNSIGNED),
    (CERT_E_REVOKED, VerificationStatus.INVALID),
    (TRUST_E_EXPLICIT_DISTRUST, VerificationStatus.INVALID),
    (CERT_E_EXPIRED, VerificationStatus.INVALID),
    (CERT_E_UNTRUSTEDROOT, VerificationStatus.INVALID),
    (CERT_E_CHAINING, VerificationStatus.INVALID),
    (TRUST_E_BAD_DIGEST, VerificationStatus.INVALID),
    (0x80092003, VerificationStatus.INVALID),          # 0x8009 家族加密错误
    (CERT_E_REVOCATION_FAILURE, VerificationStatus.NOT_VERIFIABLE),
    (CRYPT_E_REVOCATION_OFFLINE, VerificationStatus.NOT_VERIFIABLE),
    (ERROR_ACCESS_DENIED, VerificationStatus.NOT_VERIFIABLE),
    (ERROR_CANCELLED, VerificationStatus.NOT_VERIFIABLE),
    (0x8000FFFF, VerificationStatus.NOT_VERIFIABLE),   # 未知码 fail-closed
])
def test_classify_unsigned_hr(hr, expected):
    v, _reason = classify_winverifytrust(hr)
    assert v is expected


@pytest.mark.parametrize("unsigned,expected", [
    (TRUST_E_NOSIGNATURE, VerificationStatus.UNSIGNED),
    (CERT_E_REVOKED, VerificationStatus.INVALID),
    (CERT_E_UNTRUSTEDROOT, VerificationStatus.INVALID),
    (CERT_E_CHAINING, VerificationStatus.INVALID),
    (CERT_E_REVOCATION_FAILURE, VerificationStatus.NOT_VERIFIABLE),
    (CRYPT_E_REVOCATION_OFFLINE, VerificationStatus.NOT_VERIFIABLE),
])
def test_classify_signed_negative_hr_not_misclassified(unsigned, expected):
    v, _reason = classify_winverifytrust(_signed(unsigned))
    assert v is expected
    assert v is not VerificationStatus.OK


# ---------------------------------------------------------------------------
# 吊销/链策略矩阵：全部非 ok 一律阻止；只有 ok 放行
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("hr,expected_blocked", [
    (0, False),                                   # 全绿 → 放行
    (TRUST_E_NOSIGNATURE, True),                  # 未签名 → 阻止
    (CERT_E_REVOKED, True),                       # 明确吊销 → 阻止
    (TRUST_E_EXPLICIT_DISTRUST, True),            # 显式不信任 → 阻止
    (CERT_E_UNTRUSTEDROOT, True),                 # 链不可信 → 阻止
    (CERT_E_CHAINING, True),                      # 链错误 → 阻止
    (TRUST_E_BAD_DIGEST, True),                   # 摘要不符 → 阻止
    (CERT_E_REVOCATION_FAILURE, True),            # 吊销未知 → 阻止
    (CRYPT_E_REVOCATION_OFFLINE, True),           # 服务器离线 → 阻止
    (ERROR_ACCESS_DENIED, True),                  # 系统拒绝 → 阻止
    (ERROR_CANCELLED, True),                      # 用户取消 → 阻止
])
def test_revocation_chain_policy_matrix(hr, expected_blocked):
    v, reason = classify_winverifytrust(hr)
    status = _status(v, failure_reason=reason)
    blocked, _msg = should_block_elevation(status)
    assert blocked is expected_blocked


def test_revocation_unknown_never_becomes_valid():
    for hr in (CERT_E_REVOCATION_FAILURE, CRYPT_E_REVOCATION_OFFLINE,
               _signed(CERT_E_REVOCATION_FAILURE)):
        v, _ = classify_winverifytrust(hr)
        assert v is VerificationStatus.NOT_VERIFIABLE
        blocked, _ = should_block_elevation(_status(v))
        assert blocked is True


# ---------------------------------------------------------------------------
# signature_stale：路径绑定 + 内容/时间变更失配
# ---------------------------------------------------------------------------
def _write(path: pathlib.Path, content: bytes) -> None:
    path.write_bytes(content)


def test_stale_false_when_unchanged(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"PE-BODY-v1")
    size, mtime_ns = file_snapshot(str(f))
    status = _status(VerificationStatus.OK, path=str(f),
                     file_size=size, file_mtime_ns=mtime_ns)
    assert signature_stale(status, str(f)) is False


def test_stale_true_when_size_changed(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"v1")
    size, mtime_ns = file_snapshot(str(f))
    status = _status(VerificationStatus.OK, path=str(f),
                     file_size=size, file_mtime_ns=mtime_ns)
    _write(f, b"v1-longer-body")  # 内容/大小变化
    assert signature_stale(status, str(f)) is True


def test_stale_true_when_mtime_changed(tmp_path):
    import os as _os
    f = tmp_path / "setup.exe"
    _write(f, b"same-bytes")
    size, mtime_ns = file_snapshot(str(f))
    status = _status(VerificationStatus.OK, path=str(f),
                     file_size=size, file_mtime_ns=mtime_ns)
    future = mtime_ns + 10 ** 9
    _os.utime(f, ns=(future, future))
    assert signature_stale(status, str(f)) is True


def test_stale_true_when_file_deleted(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    size, mtime_ns = file_snapshot(str(f))
    status = _status(VerificationStatus.OK, path=str(f),
                     file_size=size, file_mtime_ns=mtime_ns)
    f.unlink()
    assert signature_stale(status, str(f)) is True


def test_stale_true_when_status_none():
    assert signature_stale(None, "C:\\x.exe") is True


def test_stale_true_when_path_changed(tmp_path):
    f = tmp_path / "a.exe"
    _write(f, b"a")
    size, mtime_ns = file_snapshot(str(f))
    status = _status(VerificationStatus.OK, path=str(f),
                     file_size=size, file_mtime_ns=mtime_ns)
    other = tmp_path / "b.exe"
    _write(other, b"a")
    assert signature_stale(status, str(other)) is True


def test_stale_true_when_snapshot_missing(tmp_path):
    f = tmp_path / "setup.exe"
    _write(f, b"x")
    # 旧结果未携带 file_size / file_mtime_ns → 保守重验
    status = _status(VerificationStatus.OK, path=str(f))
    assert signature_stale(status, str(f)) is True


# ---------------------------------------------------------------------------
# skip_signature_check 隔离：UI 源码无 True 字面量；Linux 守卫不绕过
# ---------------------------------------------------------------------------
def test_print_export_never_passes_skip_signature_check_true():
    text = _PRINT_EXPORT_PY.read_text(encoding="utf-8")
    offenders = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "skip_signature_check" in line and "True" in line:
            offenders.append(f"line {lineno}: {line.strip()}")
        if "allow_unsigned" in line and "True" in line:
            offenders.append(f"line {lineno}: {line.strip()}")
    assert not offenders, "生产 UI 不得开启跳过签名检查：" + "; ".join(offenders)


def test_elevate_process_skip_flag_still_guarded_on_linux():
    with pytest.raises(PrintError) as exc:
        elevate_process(r"C:\fake\setup.exe", skip_signature_check=True)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# 签名者提取：fake provider 模拟 CryptQueryObject（不触达 Windows）
# ---------------------------------------------------------------------------
class _FakeProvider:
    def __init__(self, query_result=None, query_exc=None, display="Zebra Technologies"):
        self._result = query_result
        self._exc = query_exc
        self.display = display
        self.freed = []
        self.closed = []
        self.enumerated = False

    def query(self, path):
        if self._exc:
            raise self._exc
        return self._result

    def enum_first(self, store):
        self.enumerated = True
        return "CERT-FROM-STORE"

    def display_name(self, ctx):
        return self.display

    def free_cert(self, ctx):
        self.freed.append(ctx)

    def close_store(self, store):
        self.closed.append(store)


def test_signer_extracted_on_success_and_freed():
    provider = _FakeProvider(query_result=("STORE-H", "CTX-OWNED", True))
    publisher, signer, timestamp = _extract_signer_info(provider, "C:\\x.exe")
    assert publisher == "Zebra Technologies"
    assert signer == "Zebra Technologies"
    assert timestamp is None  # 时间戳未提取，如实 None
    assert provider.freed == ["CTX-OWNED"]
    assert provider.closed == ["STORE-H"]


def test_signer_fallback_enum_first_from_store():
    provider = _FakeProvider(query_result=("STORE-H", None, False))
    publisher, _signer, _ts = _extract_signer_info(provider, "C:\\x.msi")
    assert publisher == "Zebra Technologies"
    assert provider.enumerated is True
    # 库枚举上下文不由调用方释放
    assert provider.freed == []
    assert provider.closed == ["STORE-H"]


def test_signer_query_failure_returns_none_no_fabrication():
    provider = _FakeProvider(query_result=None)
    assert _extract_signer_info(provider, "C:\\x.exe") == (None, None, None)
    assert provider.closed == []


def test_signer_os_error_returns_none():
    provider = _FakeProvider(query_exc=OSError("boom"))
    assert _extract_signer_info(provider, "C:\\x.exe") == (None, None, None)


def test_signer_blank_display_name_returns_none():
    provider = _FakeProvider(query_result=("STORE-H", "CTX", True), display="   ")
    assert _extract_signer_info(provider, "C:\\x.exe") == (None, None, None)
    assert provider.freed == ["CTX"]  # 仍释放自有上下文


def test_non_ok_status_publisher_none_and_ui_shows_unknown():
    for v in (VerificationStatus.UNSIGNED, VerificationStatus.INVALID,
              VerificationStatus.NOT_VERIFIABLE):
        status = _status(v, publisher=None)
        text = describe_signature_status(status)
        assert "发布者：未知" in text
        assert status.publisher is None


def test_ok_with_extracted_publisher_shows_real_name():
    status = _status(VerificationStatus.OK, publisher="Zebra Technologies")
    text = describe_signature_status(status)
    assert "Zebra Technologies" in text
    assert "发布者：未知" not in text


# ---------------------------------------------------------------------------
# EXE / MSI 同一验证入口（Linux 守卫一致抛错，不分叉伪造）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["setup.exe", "pkg.msi", "SETUP.EXE", "PKG.MSI"])
def test_exe_msi_share_verify_entry_on_linux_raises(name):
    with pytest.raises(PrintError) as exc:
        verify_authenticode(str(_REPO_ROOT / name))
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# Windows 分支源码断言：真实实现字面量存在（非占位/mock）
# ---------------------------------------------------------------------------
def test_windows_branch_real_implementation_literals():
    text = _AUTHENTICODE_PY.read_text(encoding="utf-8")
    for literal in (
        "WTD_STATEACTION_VERIFY",
        "WTD_STATEACTION_CLOSE",
        "dwStateAction",
        "cbStruct",
        "WTD_REVOKE_WHOLECHAIN",
        "fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN",
        "CertFreeCertificateContext",
        "CryptQueryObject",
        "CertGetNameStringW",
        "CertCloseStore",
        "normalize_hresult",
        "restype = ctypes.c_long",
    ):
        assert literal in text, f"缺少 Windows 真实实现字面量: {literal}"


def test_revocation_check_not_disabled_in_source():
    text = _AUTHENTICODE_PY.read_text(encoding="utf-8")
    # 真实调用处不得把吊销检查设为 NONE
    assert "fdwRevocationChecks = WTD_REVOKE_NONE" not in text
