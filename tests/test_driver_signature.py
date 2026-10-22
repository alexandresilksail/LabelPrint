"""Authenticode 签名闸门与提权命令计划的纯逻辑测试（Linux 全量可跑）。

覆盖：
- 路径含空格/中文：parse_elevate_request 列表参数正确保留，无拼接、无注入。
- 恶意参数（``/I``、``;cmd``、``&&``、引号注入等）被当作普通列表项。
- 无效文件（不存在 / 扩展名不支持 .pdf）→ 阻止。
- 签名状态矩阵：ok/unsigned/invalid/not_verifiable/unsupported_extension
  × should_block_elevation 预期。
- EXE vs MSI 分路径：command 分别为 [exe, *args] 与 [msiexec.exe, /i, msi]，
  永不包含 /qn 静默标志；MSI 自带静默标志也被剔除。
- ShellExecuteW 返回码 → 提权结果映射（UAC 取消单独识别）。
- 提权闸门结果对象（BLOCKED + blocked_reason）。
- Linux 守卫：elevate_process / verify_authenticode 抛 UNSUPPORTED_PLATFORM，
  绝不伪造 Windows API 调用。
"""

import pytest

from client.printing.authenticode import (
    SIGNED_OK_CAUTION,
    SignatureStatus,
    VerificationStatus,
    build_shellexec_parameters,
    describe_signature_status,
    detect_installer_kind,
    elevation_blocked_message,
    map_elevation_outcome,
    parse_elevate_request,
    quote_argument,
    should_block_elevation,
    verify_authenticode,
)
from client.printing.driver_installer import (
    ElevationOutcome,
    ElevationResult,
    elevate_process,
)
from client.printing.errors import PrintError


def _status(verification: VerificationStatus, **kw) -> SignatureStatus:
    base = dict(path="C:\\fake\\file.exe", file_exists=True,
                verification=verification, failure_reason=None)
    base.update(kw)
    return SignatureStatus(**base)


# ---------------------------------------------------------------------------
# 路径含空格 / 中文：列表参数原样保留，不拼接
# ---------------------------------------------------------------------------
def test_exe_path_with_spaces_and_chinese_preserves_tokens():
    path = r"C:\Program Files\厂商 驱动\setup v2.exe"
    plan = parse_elevate_request(path, ["/S", "--lang=zh-CN"])
    assert plan.is_msi is False
    assert plan.supported is True
    # 路径原样作为第一个令牌，附加参数各自独立成项
    assert plan.command == (path, "/S", "--lang=zh-CN")
    assert plan.args == ("/S", "--lang=zh-CN")


def test_msi_path_with_chinese_directory_uses_msiexec_i():
    path = r"C:\驱动\中文目录\安装程序.msi"
    plan = parse_elevate_request(path, ["/norestart"])
    assert plan.is_msi is True
    assert plan.supported is True
    assert plan.command == ("msiexec.exe", "/i", path, "/norestart")
    # 绝不自带 /qn 静默标志
    assert not any(tok.lower() == "/qn" for tok in plan.command)


def test_installer_kind_detection_case_insensitive():
    assert detect_installer_kind("a.EXE") == "exe"
    assert detect_installer_kind("b.MSI") == "msi"
    assert detect_installer_kind("c.pdf") == "unsupported"
    assert detect_installer_kind("") == "unsupported"


# ---------------------------------------------------------------------------
# 恶意参数：只作为普通列表项，不产生 shell 拼接注入
# ---------------------------------------------------------------------------
INJECTION_ARGS = [
    "/I", ";cmd", "&&", '" & calc.exe"', "| dir",
    "%PATH%", "& whoami", "`id`", "$(reboot)",
]


def test_malicious_args_are_plain_list_items_no_injection():
    path = r"D:\官方\driver.exe"
    plan = parse_elevate_request(path, INJECTION_ARGS)
    # 每个恶意串都完整保留为独立令牌，不被拆分、不被拼接成 shell 命令
    assert plan.args == tuple(INJECTION_ARGS)
    assert plan.command == (path, *INJECTION_ARGS)
    # 不存在把多令牌连成一条命令字符串的产物
    joined = " ".join(plan.command)
    assert "&&" in joined  # 原文仍在，但只是参数文本，不经 shell 解释
    assert ";cmd" in joined
    # 列表结构：没有任何令牌内部出现被二次拼接的命令分隔符组合
    for tok in plan.command:
        assert isinstance(tok, str)


def test_msi_malicious_args_pass_through_as_plain_tokens():
    path = r"D:\官方\pkg.msi"
    plan = parse_elevate_request(path, ["&&", ";cmd", "/I"])
    assert plan.command[0] == "msiexec.exe"
    assert plan.command[1] == "/i"
    assert plan.command[2] == path
    assert plan.args == ("&&", ";cmd", "/I")


# ---------------------------------------------------------------------------
# None / 空参数规范化
# ---------------------------------------------------------------------------
def test_none_and_blank_args_normalized_away():
    plan = parse_elevate_request("x.exe", [None, "", "   ", "--keep=1"])
    assert plan.args == ("--keep=1",)
    assert plan.command == ("x.exe", "--keep=1")


def test_none_args_and_empty_path():
    plan = parse_elevate_request("   ", None)
    assert plan.supported is False
    assert plan.command == ()


# ---------------------------------------------------------------------------
# 无效文件 / 扩展名不支持 → 阻止
# ---------------------------------------------------------------------------
def test_pdf_extension_unsupported_plan_blocked():
    plan = parse_elevate_request(r"C:\docs\manual.pdf")
    assert plan.supported is False
    assert plan.is_msi is False
    assert plan.command == ()
    blocked, reason = should_block_elevation(
        _status(VerificationStatus.UNSUPPORTED_EXTENSION,
                path=r"C:\docs\manual.pdf", file_exists=True,
                failure_reason="仅支持 .exe / .msi。"))
    assert blocked is True
    assert reason


def test_missing_file_not_verifiable_blocked():
    status = SignatureStatus(
        path=r"C:\nope\setup.exe", file_exists=False,
        verification=VerificationStatus.NOT_VERIFIABLE,
        failure_reason="文件不存在，无法验证签名。")
    blocked, reason = should_block_elevation(status)
    assert blocked is True
    assert "阻止" in reason or "默认" in reason


# ---------------------------------------------------------------------------
# 签名状态矩阵
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("verification,expected_blocked", [
    (VerificationStatus.OK, False),
    (VerificationStatus.UNSIGNED, True),
    (VerificationStatus.INVALID, True),
    (VerificationStatus.NOT_VERIFIABLE, True),
    (VerificationStatus.UNSUPPORTED_EXTENSION, True),
])
def test_status_matrix(verification, expected_blocked):
    blocked, reason = should_block_elevation(_status(verification))
    assert blocked is expected_blocked
    assert reason


def test_ok_reason_is_official_source_caution_not_blanket_safe():
    blocked, reason = should_block_elevation(_status(VerificationStatus.OK))
    assert blocked is False
    assert reason == SIGNED_OK_CAUTION
    # 诚信红线：必须明示“签名有效≠绝对安全”并提示核对官方来源
    assert "签名有效" in reason
    assert "绝对安全" in reason
    assert "官方来源" in reason


@pytest.mark.parametrize("verification", [
    VerificationStatus.UNSIGNED,
    VerificationStatus.INVALID,
    VerificationStatus.NOT_VERIFIABLE,
    VerificationStatus.UNSUPPORTED_EXTENSION,
])
def test_blocked_reasons_are_human_readable(verification):
    blocked, reason = should_block_elevation(_status(verification))
    assert blocked is True
    assert reason and "阻止" in reason


# ---------------------------------------------------------------------------
# EXE vs MSI 分路径；MSI 静默标志剔除；不出现 /qn
# ---------------------------------------------------------------------------
def test_exe_launch_plan_direct_executable():
    plan = parse_elevate_request(r"C:\d\setup.exe", ["/a", "/b"])
    assert plan.command == (r"C:\d\setup.exe", "/a", "/b")
    assert "msiexec.exe" not in plan.command


def test_msi_strips_silent_flags_but_keeps_visible_args():
    plan = parse_elevate_request(
        r"C:\d\pkg.msi", ["/qn", "/quiet", "/passive", "/QB!", "/norestart"])
    assert plan.command[:3] == ("msiexec.exe", "/i", r"C:\d\pkg.msi")
    # 所有静默标志被剔除
    lowered = {tok.lower() for tok in plan.command}
    for silent in ("/qn", "/quiet", "/passive", "/qb!"):
        assert silent not in lowered
    # 可见性参数保留
    assert "/norestart" in plan.command


def test_no_qn_silent_flag_anywhere_in_msi_plan():
    plan = parse_elevate_request(r"C:\d\pkg.msi", ["/QN"])
    assert "/qn" not in [t.lower() for t in plan.command]
    assert plan.command == ("msiexec.exe", "/i", r"C:\d\pkg.msi")


# ---------------------------------------------------------------------------
# 参数引用与 ShellExecuteW 参数串（不经 cmd.exe，元字符原样保留）
# ---------------------------------------------------------------------------
def test_quote_argument_wraps_spaces_and_escapes_quotes():
    assert quote_argument("plain") == "plain"
    assert quote_argument("with space") == '"with space"'
    assert quote_argument('say "hi"') == '"say \\"hi\\""'
    # 元字符不被删除或解释
    assert quote_argument("&&") == "&&"
    assert quote_argument(";cmd") == ";cmd"


def test_build_parameters_msi_i_quoted_path_no_shell_join():
    plan = parse_elevate_request(r"C:\My Pkg\p.msi", ["/norestart"])
    params = build_shellexec_parameters(plan)
    assert params.startswith('/i "C:\\My Pkg\\p.msi"')
    assert "/norestart" in params
    assert "/qn" not in params.lower()


def test_build_parameters_exe_only_user_args():
    plan = parse_elevate_request(r"C:\d\setup.exe", ["--x=1", "a b"])
    params = build_shellexec_parameters(plan)
    assert "--x=1" in params
    assert '"a b"' in params


# ---------------------------------------------------------------------------
# ShellExecuteW 返回码映射（UAC 取消单独识别）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code,expected", [
    (42, "launched"),
    (33, "launched"),
    (12, "cancelled_by_user"),
    (5, "requires_uac"),
    (2, "failed"),
    (31, "failed"),
    (0, "failed"),
])
def test_map_elevation_outcome(code, expected):
    assert map_elevation_outcome(code) == expected


# ---------------------------------------------------------------------------
# 闸门结果对象：BLOCKED 携带 blocked_reason
# ---------------------------------------------------------------------------
def test_blocked_outcome_carries_reason():
    outcome = ElevationOutcome(
        ElevationResult.BLOCKED,
        blocked_reason="文件未进行 Authenticode 数字签名，已默认阻止提权。")
    assert outcome.status is ElevationResult.BLOCKED
    assert "阻止" in outcome.blocked_reason


def test_launched_outcome_has_no_blocked_reason():
    outcome = ElevationOutcome(ElevationResult.LAUNCHED)
    assert outcome.status is ElevationResult.LAUNCHED
    assert outcome.blocked_reason is None


# ---------------------------------------------------------------------------
# UI 纯文案：状态标签 / 描述 / 阻止提示
# ---------------------------------------------------------------------------
def test_describe_signature_status_includes_publisher_path_reason():
    status = SignatureStatus(
        path=r"C:\d\setup.exe", file_exists=True,
        verification=VerificationStatus.INVALID,
        publisher="Fake Corp", failure_reason="摘要不匹配。")
    text = describe_signature_status(status)
    assert "签名无效" in text
    assert "Fake Corp" in text
    assert r"C:\d\setup.exe" in text
    assert "摘要不匹配" in text


def test_blocked_message_mentions_status_and_reason():
    status = _status(VerificationStatus.UNSIGNED)
    msg = elevation_blocked_message(status)
    assert "已阻止提权" in msg
    assert "未签名" in msg


def test_ok_describe_shows_publisher_and_caution_available():
    status = _status(VerificationStatus.OK, publisher="Zebra Technologies")
    text = describe_signature_status(status)
    assert "签名有效" in text
    assert "Zebra Technologies" in text
    assert SIGNED_OK_CAUTION  # 放行提示常量存在且含官方来源


# ---------------------------------------------------------------------------
# Linux 守卫：不伪造 Windows API
# ---------------------------------------------------------------------------
def test_authenticode_unavailable_on_linux():
    from client.printing import authenticode
    assert authenticode.authenticode_available() is False


def test_verify_authenticode_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        verify_authenticode(r"C:\fake\setup.exe")
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_verify_authenticode_empty_path_returns_not_verifiable():
    status = verify_authenticode("")
    assert status.verification is VerificationStatus.NOT_VERIFIABLE
    blocked, _ = should_block_elevation(status)
    assert blocked is True


def test_elevate_process_raises_on_linux_back_compat_positional():
    # 历史位置参数调用方式必须仍然得到平台守卫（不回归）
    with pytest.raises(PrintError) as exc:
        elevate_process(r"C:\fake\setup.exe", ["/install"])
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_elevate_process_guard_blocks_even_with_skip_flag_on_linux():
    with pytest.raises(PrintError) as exc:
        elevate_process(r"C:\fake\setup.exe", skip_signature_check=True)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
