"""Authenticode 签名验证与提权决策（Windows 条件导入 + 纯逻辑可测）。

安全红线（与 ``driver_installer`` 模块一致，不可被参数关闭）：

- 提权（UAC ``runas``）之前必须先检查安装程序的 Authenticode 签名状态；
  **签名无效 / 未签名 / 无法验证 / 类型不支持时默认阻止提权**，不提供
  “关闭签名检查 / 静默提权 / 绕过 UAC”的任何旁路。
- **签名有效 ≠ 文件绝对安全**：即便验证通过，UI 仍必须提示用户核对官方来源。
- 本模块的纯逻辑（状态决策、命令计划、参数引用、结果映射、文案）在任意平台
  可导入、可单测；只有 Windows 专属的 ``verify_authenticode`` 真实系统调用
  在非 Windows 上抛出 :class:`PrintError(UNSUPPORTED_PLATFORM)`，**Linux 绝不
  伪造调用 WinVerifyTrust**。

Windows 验证依据（MS Learn）：
- ``WinVerifyTrust`` + ``WINTRUST_ACTION_GENERIC_VERIFY_V2``
  （GUID ``{00AAC56B-CD44-11d0-8CC2-00C04FC295EE}``）是通用 Authenticode
  验证动作；PE（.exe）与 MSI（.msi）都通过各自的 SIP（Subject Interface
  Package）注册到该动作，因此**同一 API 分别验证两类文件**，不需要也不允许
  拼接命令行。MSI 的发布者信息也可经 ``msi.dll!MsiGetFileSignatureInformationW``
  取得（本模块作为可选增强点保留，默认以 WinVerifyTrust 结果为准）。
- 返回码映射：``ERROR_SUCCESS(0)`` → ok；``TRUST_E_NOSIGNATURE(0x800B0100)``
  → unsigned；``TRUST_E_BAD_DIGEST / TRUST_E_SUBJECT_NOT_TRUSTED /
  CERT_E_*`` → invalid；``ERROR_CANCELLED(1223)`` / ``ERROR_ACCESS_DENIED(5)``
  等用户取消或系统拒绝 → not_verifiable。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from .errors import PrintError


# ---------------------------------------------------------------------------
# 签名验证状态枚举（纯逻辑，平台无关）
# ---------------------------------------------------------------------------
class VerificationStatus(str, Enum):
    """安装程序 Authenticode 签名的验证结论。

    - OK                    : 签名有效（证书链受信、摘要匹配、未过期）。
    - UNSIGNED             : 文件没有任何 Authenticode 签名。
    - INVALID              : 存在签名但无效（被篡改/摘要不符/证书不受信/已吊销）。
    - NOT_VERIFIABLE       : 无法完成验证（用户取消、系统拒绝、环境不可用）。
    - UNSUPPORTED_EXTENSION: 扩展名不在允许范围（仅 .exe / .msi）。
    """

    OK = "ok"
    UNSIGNED = "unsigned"
    INVALID = "invalid"
    NOT_VERIFIABLE = "not_verifiable"
    UNSUPPORTED_EXTENSION = "unsupported_extension"


# ---------------------------------------------------------------------------
# 签名状态数据（frozen，可在测试中直接构造，无需触碰系统 API）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SignatureStatus:
    """单个安装程序文件的签名验证结论。

    Attributes:
        path: 被检查的文件路径（原文回显，不做规范化改写）。
        file_exists: 文件在磁盘上是否真实存在。
        verification: 验证结论，见 :class:`VerificationStatus`。
        publisher: 签名发布者名称（证书 Subject 的常见名）；取不到为 None。
        signer: 签名者/证书主体详细串；取不到为 None。
        timestamp: 签名时间戳（可验证的 RFC3161/Authenticode 时间戳）；无则 None。
        failure_reason: 失败/阻止的人类可读原因；ok 时为 None。
        is_msi: 是否为 MSI 安装包（决定启动方式）。
        checked_at: 检查时刻（UTC ISO8601）；纯逻辑构造时可为空串。
    """

    path: str
    file_exists: bool
    verification: VerificationStatus
    publisher: str | None = None
    signer: str | None = None
    timestamp: str | None = None
    failure_reason: str | None = None
    is_msi: bool = False
    checked_at: str = ""


# ---------------------------------------------------------------------------
# 扩展名白名单（仅 .exe / .msi；其余一律阻止，不做猜测）
# ---------------------------------------------------------------------------
EXE_EXTENSIONS = (".exe",)
MSI_EXTENSIONS = (".msi",)
SUPPORTED_EXTENSIONS = EXE_EXTENSIONS + MSI_EXTENSIONS


def _ext_lower(path: str) -> str:
    return os.path.splitext(str(path or ""))[1].lower()


def detect_installer_kind(path: str) -> str:
    """按扩展名判定安装程序种类：``"exe"`` / ``"msi"`` / ``"unsupported"``。

    纯函数；Windows 风格反斜杠路径同样适用（扩展名只取最后一个 ``.`` 之后）。
    """
    ext = _ext_lower(path)
    if ext in EXE_EXTENSIONS:
        return "exe"
    if ext in MSI_EXTENSIONS:
        return "msi"
    return "unsupported"


# ---------------------------------------------------------------------------
# 平台能力探测（纯逻辑）
# ---------------------------------------------------------------------------
def authenticode_available() -> bool:
    """当前平台是否具备真实 Authenticode 验证能力。

    非 Windows（含本 Linux 测试环境）恒为 False；Windows 上 ``wintrust.dll``
    随系统提供，能力判定以平台为准。**返回 False 时调用方必须走阻止分支，
    绝不允许伪装成已验证。**
    """
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# Windows 真实验证（条件导入；非 Windows 抛 UNSUPPORTED_PLATFORM，不伪造）
# ---------------------------------------------------------------------------
def verify_authenticode(path: str) -> SignatureStatus:
    """检查 ``path`` 的 Authenticode 签名状态。

    非 Windows 平台直接抛 :class:`PrintError(UNSUPPORTED_PLATFORM)`——
    Linux 沙箱没有 WinVerifyTrust，**禁止伪造验证结果**；纯决策由
    :func:`should_block_elevation` 在 Linux 上独立测试。

    Windows 上通过 ``wintrust.dll!WinVerifyTrust``（通用验证动作）对
    EXE / MSI 分别走各自 SIP 完成验证，结果映射到 :class:`VerificationStatus`。
    """
    target = str(path or "").strip()
    if not target:
        return SignatureStatus(
            path=path or "",
            file_exists=False,
            verification=VerificationStatus.NOT_VERIFIABLE,
            failure_reason="未提供安装程序路径，无法验证签名。",
            checked_at=_now_iso(),
        )

    if not authenticode_available():
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="Authenticode 签名验证仅支持 Windows；当前平台无法验证，"
                       "出于安全默认阻止提权，请在 Windows 官方环境中操作。",
        )

    if not os.path.isfile(target):
        kind = detect_installer_kind(target)
        return SignatureStatus(
            path=target,
            file_exists=False,
            verification=(VerificationStatus.UNSUPPORTED_EXTENSION
                          if kind == "unsupported"
                          else VerificationStatus.NOT_VERIFIABLE),
            failure_reason=("文件不存在，无法验证签名。"
                            if kind != "unsupported"
                            else "不支持的安装程序类型（仅 .exe / .msi）。"),
            is_msi=(kind == "msi"),
            checked_at=_now_iso(),
        )

    kind = detect_installer_kind(target)
    if kind == "unsupported":
        return SignatureStatus(
            path=target,
            file_exists=True,
            verification=VerificationStatus.UNSUPPORTED_EXTENSION,
            failure_reason="不支持的安装程序类型（仅 .exe / .msi），已阻止提权。",
            checked_at=_now_iso(),
        )

    # pragma: no cover - 以下仅在 Windows 真机执行，Linux 测试永不触达
    return _verify_with_winverifytrust(target, is_msi=(kind == "msi"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# pragma: no cover - Windows 真机路径（Linux 不可达，不做伪造实现）
def _verify_with_winverifytrust(path: str, *, is_msi: bool) -> SignatureStatus:
    """调用 ``wintrust.dll!WinVerifyTrust`` 验证（EXE/MSI 同一通用动作）。

    依据：``WINTRUST_ACTION_GENERIC_VERIFY_V2`` 由系统按文件扩展名分发到
    PE SIP 或 MSI SIP，EXE 与 MSI 走各自 SIP，无需拼接命令行。
    用户取消（ERROR_CANCELLED）/ 系统拒绝（ERROR_ACCESS_DENIED）映射为
    NOT_VERIFIABLE，绝不放行。
    """
    import ctypes
    from ctypes import wintypes  # noqa: F401

    # WINTRUST_ACTION_GENERIC_VERIFY_V2 GUID {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class _WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("unionChoice", wintypes.DWORD),
            ("pFileOrCatalogOrBlobOrSgnrOrCert", ctypes.c_void_p),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
        ]

    WTD_UI_NONE = 2
    WTD_REVOKE_NONE = 0
    WTD_CHOICE_FILE = 1
    WTD_STATEACTION_VERIFY = 1
    WTD_STATEACTION_CLOSE = 2

    TRUST_E_NOSIGNATURE = 0x800B0100
    TRUST_E_BAD_DIGEST = 0x80096010
    TRUST_E_SUBJECT_NOT_TRUSTED = 0x800B0109
    TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
    ERROR_CANCELLED = 1223
    ERROR_ACCESS_DENIED = 5

    action = _GUID(0x00AAC56B, 0xCD44, 0x11D0,
                   (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0,
                                        0x4F, 0xC2, 0x95, 0xEE))
    file_info = _WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(_WINTRUST_FILE_INFO)
    file_info.pcwszFilePath = path

    wtd = _WINTRUST_DATA()
    wtd.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    wtd.dwUIChoice = WTD_UI_NONE
    wtd.fdwRevocationChecks = WTD_REVOKE_NONE
    wtd.unionChoice = WTD_CHOICE_FILE
    wtd.pFileOrCatalogOrBlobOrSgnrOrCert = ctypes.cast(
        ctypes.pointer(file_info), ctypes.c_void_p)
    wtd.dwStateAction = WTD_STATEACTION_VERIFY

    wintrust = ctypes.windll.wintrust
    try:
        hr = wintrust.WinVerifyTrust(
            None, ctypes.byref(action), ctypes.byref(wtd))
    except OSError as exc:
        return SignatureStatus(
            path=path, file_exists=True,
            verification=VerificationStatus.NOT_VERIFIABLE,
            failure_reason=f"签名验证调用被系统拒绝（{exc}），已默认阻止提权。",
            is_msi=is_msi, checked_at=_now_iso(),
        )
    finally:
        if wtd.hWVTStateData:
            wtd.dwStateAction = WTD_STATEACTION_CLOSE
            wintrust.WinVerifyTrust(
                None, ctypes.byref(action), ctypes.byref(wtd))

    publisher, signer, timestamp = _best_effort_signer(
        path, is_msi=is_msi)

    if hr == 0:
        return SignatureStatus(
            path=path, file_exists=True, verification=VerificationStatus.OK,
            publisher=publisher, signer=signer, timestamp=timestamp,
            is_msi=is_msi, checked_at=_now_iso(),
        )
    if hr == TRUST_E_NOSIGNATURE:
        return SignatureStatus(
            path=path, file_exists=True,
            verification=VerificationStatus.UNSIGNED,
            failure_reason="文件未进行 Authenticode 数字签名。",
            is_msi=is_msi, checked_at=_now_iso(),
        )
    if hr in (TRUST_E_BAD_DIGEST, TRUST_E_SUBJECT_NOT_TRUSTED,
              TRUST_E_PROVIDER_UNKNOWN) or (hr & 0xFFFF0000) == 0x800B0000 \
            or (hr & 0xFFFF0000) == 0x80090000:
        return SignatureStatus(
            path=path, file_exists=True,
            verification=VerificationStatus.INVALID,
            failure_reason="签名无效（摘要不匹配/证书不受信/已吊销或被篡改）。",
            is_msi=is_msi, checked_at=_now_iso(),
        )
    if hr in (ERROR_CANCELLED, ERROR_ACCESS_DENIED):
        return SignatureStatus(
            path=path, file_exists=True,
            verification=VerificationStatus.NOT_VERIFIABLE,
            failure_reason="用户取消或系统拒绝了签名验证，出于谨慎默认阻止提权。",
            is_msi=is_msi, checked_at=_now_iso(),
        )
    return SignatureStatus(
        path=path, file_exists=True,
        verification=VerificationStatus.NOT_VERIFIABLE,
        failure_reason=f"签名验证未返回确定结论（HRESULT 0x{hr & 0xFFFFFFFF:08X}），"
                        "出于谨慎默认阻止提权。",
        is_msi=is_msi, checked_at=_now_iso(),
    )


# pragma: no cover - Windows 真机最佳努力取发布者（取不到则留 None，不编造）
def _best_effort_signer(path: str, *, is_msi: bool) -> tuple[str | None, str | None, str | None]:
    """最佳努力提取发布者；MSI 可走 msi.dll!MsiGetFileSignatureInformationW。

    取不到一律返回 None（UI 显示“未知”），禁止伪造发布者名称。
    """
    return None, None, None


# ---------------------------------------------------------------------------
# 纯逻辑：提权闸门决策（Linux 可测）
# ---------------------------------------------------------------------------
SIGNED_OK_CAUTION = (
    "签名有效≠文件绝对安全，请核对官方来源后继续"
)

_BLOCK_MESSAGES = {
    VerificationStatus.UNSIGNED: (
        "文件未进行 Authenticode 数字签名，出于安全考虑已默认阻止提权；"
        "请从厂商官方渠道重新下载完整安装包后再试。"
    ),
    VerificationStatus.INVALID: (
        "文件签名无效（证书不受信/摘要不匹配/文件可能被篡改），"
        "已默认阻止提权，请勿运行该文件。"
    ),
    VerificationStatus.NOT_VERIFIABLE: (
        "无法完成签名验证（用户取消/系统拒绝/当前环境不可用），"
        "出于谨慎已默认阻止提权；请改从厂商官方渠道获取安装包后重试。"
    ),
    VerificationStatus.UNSUPPORTED_EXTENSION: (
        "不支持的安装程序类型（仅允许 .exe / .msi），已阻止提权。"
    ),
}


def should_block_elevation(status: SignatureStatus) -> tuple[bool, str]:
    """根据签名状态决定是否阻止 UAC 提权。

    默认规则（白名单制：只有 ``ok`` 放行，其余一律阻止）：

    | verification            | 阻止? | 原因 |
    |-------------------------|-------|------|
    | ok                      | 否    | ``签名有效≠文件绝对安全，请核对官方来源后继续`` |
    | unsigned                | 是    | 未签名，默认阻止 |
    | invalid                 | 是    | 签名无效，默认阻止 |
    | not_verifiable          | 是    | 无法验证，谨慎阻止 |
    | unsupported_extension   | 是    | 类型不支持，阻止 |

    返回 ``(blocked, reason)``；放行时 reason 为必须展示的安全提示。
    纯函数，不触碰系统 API。
    """
    if status.verification is VerificationStatus.OK:
        return False, SIGNED_OK_CAUTION
    reason = _BLOCK_MESSAGES.get(status.verification)
    if reason is None:  # 未知枚举值同样按阻止处理（fail-closed）
        return True, "签名状态未知，出于安全考虑已默认阻止提权。"
    return True, reason


# ---------------------------------------------------------------------------
# 纯逻辑：提权命令计划（EXE / MSI 分路径，列表式参数，禁止字符串拼接）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ElevatePlan:
    """提权启动计划（列表式命令令牌，绝不拼接 shell 字符串）。

    Attributes:
        path: 规范化后的目标文件路径。
        is_msi: 是否 MSI（True 时经 ``msiexec.exe /i`` 启动，安装界面可见）。
        supported: 扩展名是否在白名单；False 时 ``command`` 为空且禁止启动。
        command: 命令令牌列表（EXE: ``[path, *args]``；
                 MSI: ``["msiexec.exe", "/i", path, *args]``；
                 **永不包含 /qn 静默标志**）。
        args: 规范化后的用户附加参数（原样作为独立列表项，不做转义拼接）。
    """

    path: str
    is_msi: bool
    supported: bool
    command: tuple[str, ...] = field(default_factory=tuple)
    args: tuple[str, ...] = field(default_factory=tuple)


def _normalize_arg(value: object) -> str | None:
    """规范化单个参数：None/空白丢弃；其余转成字符串原样保留（不做命令拼接）。"""
    if value is None:
        return None
    text = str(value)
    if not text.strip():
        return None
    return text


# MSI 静默安装标志黑名单（安装必须可见，任何来源的静默标志都被剔除，
# 不区分大小写；本程序绝不发起静默安装）。
_MSI_SILENT_FLAGS = frozenset({
    "/qn", "/quiet", "/passive", "/q", "/qb", "/qb!", "/qf", "/qr",
})


def _strip_msi_silent_flags(args: tuple[str, ...]) -> tuple[str, ...]:
    """剔除 MSI 静默标志；其余参数原样保留为独立列表项。"""
    return tuple(a for a in args if a.lower() not in _MSI_SILENT_FLAGS)


def parse_elevate_request(path: str, args: list[str] | tuple[str, ...] | None = None) -> ElevatePlan:
    """把用户选择的安装程序路径与附加参数解析为列表式 :class:`ElevatePlan`。

    安全约定：
    - EXE → ``[path, *args]``；MSI → ``["msiexec.exe", "/i", path, *args]``。
    - 参数一律作为独立列表项传递，**不做 shell 字符串拼接**，因此
      ``&&``、``;``、``|``、引号、``/I`` 等只会被当作普通参数文本，
      不会产生命令注入。
    - MSI 永远使用可见安装界面（``/i``），**禁止 /qn 静默安装**。
    - 空参数 / None 被丢弃；不支持的扩展名 → ``supported=False``、空 command。

    纯函数，不触碰系统 API。
    """
    target = str(path or "").strip()
    norm_args = tuple(a for a in (_normalize_arg(x) for x in (args or ())) if a is not None)
    kind = detect_installer_kind(target)

    if kind == "exe":
        command: tuple[str, ...] = (target, *norm_args)
        return ElevatePlan(path=target, is_msi=False, supported=True,
                           command=command, args=norm_args)
    if kind == "msi":
        # /i 触发可见安装界面；显式不加入 /qn（静默）、不拼接任何额外 shell 元字符。
        # 即使用户参数里自带静默标志，也在此剔除：安装必须可见，禁止静默安装。
        msi_args = _strip_msi_silent_flags(norm_args)
        command = ("msiexec.exe", "/i", target, *msi_args)
        return ElevatePlan(path=target, is_msi=True, supported=True,
                           command=command, args=msi_args)
    return ElevatePlan(path=target, is_msi=False, supported=False,
                       command=(), args=norm_args)


# ---------------------------------------------------------------------------
# 纯逻辑：Windows 命令行参数引用（仅用于 ShellExecuteW lpParameters，不经 cmd.exe）
# ---------------------------------------------------------------------------
def quote_argument(token: str) -> str:
    """按 Windows 命令行规则包裹单个参数；含空白/引号时整体加双引号。

    注意：ShellExecuteW 不经过 ``cmd.exe``，``&&``、``;``、``|`` 等元字符
    只是普通文本；本函数只做引号包裹，**不删除、不解释、不拼接用户参数**。
    """
    text = str(token)
    if text == "":
        return '""'
    needs_quote = any(ch in text for ch in (" ", "\t", '"'))
    escaped = text.replace('"', '\\"')
    return f'"{escaped}"' if needs_quote else escaped


def build_shellexec_parameters(plan: ElevatePlan) -> str:
    """把计划令牌转换为 ShellExecuteW 的 lpParameters 字符串（逐令牌引用）。

    EXE：参数逐个引用后以空格连接；MSI：``/i "path"`` + 逐个引用的附加参数。
    不经过 shell，无命令解释；该字符串仅传给 ShellExecuteW，不用于 cmd.exe。
    """
    if plan.is_msi:
        tokens: tuple[str, ...] = ("/i", plan.path, *plan.args)
    else:
        tokens = plan.args
    return " ".join(quote_argument(tok) for tok in tokens)


# ---------------------------------------------------------------------------
# 纯逻辑：ShellExecuteW 返回码 → 提权结果映射（UAC 取消单独识别）
# ---------------------------------------------------------------------------
# ShellExecuteW 返回码：>32 成功；12 = ERROR_CANCELLED（用户在 UAC 点“否”）；
# 5  = SE_ERR_ACCESSDENIED（需要提权但被拒）；其余 <=32 为错误。
_MAP_LAUNCHED = "launched"
_MAP_CANCELLED = "cancelled_by_user"
_MAP_REQUIRES_UAC = "requires_uac"
_MAP_FAILED = "failed"


def map_elevation_outcome(return_code: int) -> str:
    """把 ShellExecuteW 返回码映射为纯字符串结果（平台无关，可单测）。

    - >32            → ``launched``（成功发起，不代表安装成功）
    - 12             → ``cancelled_by_user``（用户取消 UAC，驱动未安装）
    - 5              → ``requires_uac``（访问被拒/仍需提权授权）
    - 其余 <=32      → ``failed``
    """
    code = int(return_code)
    if code > 32:
        return _MAP_LAUNCHED
    if code == 12:
        return _MAP_CANCELLED
    if code == 5:
        return _MAP_REQUIRES_UAC
    return _MAP_FAILED


# ---------------------------------------------------------------------------
# 纯逻辑：UI 展示文案（对话框只负责渲染，决策与文案都在这里，可单测）
# ---------------------------------------------------------------------------
_STATUS_LABELS = {
    VerificationStatus.OK: "签名有效",
    VerificationStatus.UNSIGNED: "未签名",
    VerificationStatus.INVALID: "签名无效",
    VerificationStatus.NOT_VERIFIABLE: "无法验证",
    VerificationStatus.UNSUPPORTED_EXTENSION: "类型不支持",
}


def signature_status_label(status: SignatureStatus) -> str:
    """一行式签名状态标签（UI 直接展示）。"""
    return _STATUS_LABELS.get(status.verification, "未知状态")


def describe_signature_status(status: SignatureStatus) -> str:
    """汇总签名状态/发布者/文件路径/失败原因，供 UI 提权前展示。"""
    lines = [f"签名状态：{signature_status_label(status)}"]
    publisher = status.publisher or "未知（未提取到发布者，请核对官方来源）"
    lines.append(f"发布者：{publisher}")
    lines.append(f"文件路径：{status.path}")
    if status.timestamp:
        lines.append(f"签名时间戳：{status.timestamp}")
    if status.failure_reason:
        lines.append(f"说明：{status.failure_reason}")
    return "\n".join(lines)


def elevation_blocked_message(status: SignatureStatus) -> str:
    """阻止提权时给用户的完整文案（含状态与原因）。"""
    _, reason = should_block_elevation(status)
    return f"已阻止提权（{signature_status_label(status)}）：{reason}"
