"""Authenticode 签名验证与提权决策（Windows 条件导入 + 纯逻辑可测）。

安全红线（与 ``driver_installer`` 模块一致，不可被参数关闭）：

- 提权（UAC ``runas``）之前必须先检查安装程序的 Authenticode 签名状态；
  **签名无效 / 未签名 / 无法验证 / 类型不支持时默认阻止提权**，不提供
  “关闭签名检查 / 静默提权 / 绕过 UAC”的任何旁路。
- **签名有效 ≠ 文件绝对安全**：即便验证通过，UI 仍必须提示用户核对官方来源。
- 本模块的纯逻辑（HRESULT 分类、状态决策、命令计划、参数引用、结果映射、
  文案、新鲜度判定）在任意平台可导入、可单测；只有 Windows 专属的
  ``verify_authenticode`` 真实系统调用在非 Windows 上抛出
  :class:`PrintError(UNSUPPORTED_PLATFORM)`，**Linux 绝不伪造调用
  WinVerifyTrust**。

Windows 验证依据（MS Learn）：
- ``WinVerifyTrust`` + ``WINTRUST_ACTION_GENERIC_VERIFY_V2``
  （GUID ``{00AAC56B-CD44-11d0-8CC2-00C04FC295EE}``）是通用 Authenticode
  验证动作；PE（.exe）与 MSI（.msi）都通过各自的 SIP（Subject Interface
  Package）注册到该动作，因此**同一 API 分别验证两类文件**，不需要也不允许
  拼接命令行。
- **吊销检查开启**：``fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN(1)``，
  对整条证书链做吊销检查；吊销状态未知 / 吊销服务器离线**不得视为有效**，
  一律归 ``not_verifiable`` 并阻止提权（fail-closed）。
- 返回码分类（``classify_winverifytrust``，纯函数可测）：
  - ``ERROR_SUCCESS(0)`` → ok；
  - ``TRUST_E_NOSIGNATURE(0x800B0100)`` → unsigned；
  - ``CERT_E_REVOKED(0x800B0105)`` / ``TRUST_E_EXPLICIT_DISTRUST(0x800B0111)``
    明确吊销/显式不信任 → invalid；
  - ``TRUST_E_BAD_DIGEST`` / ``CERT_E_EXPIRED`` / ``CERT_E_UNTRUSTEDROOT`` /
    ``CERT_E_CHAINING`` 等签名/链错误 → invalid；
  - ``CERT_E_REVOCATION_FAILURE(0x800B010E)`` /
    ``CRYPT_E_REVOCATION_OFFLINE(0x80092013)`` 吊销未知/网络不可用 →
    not_verifiable；
  - ``ERROR_CANCELLED(1223)`` / ``ERROR_ACCESS_DENIED(5)`` 用户取消/系统拒绝 →
    not_verifiable。

诚信红线：未知状态绝不转有效；不虚构签名者名称、不虚构吊销结果；
Windows 真机 WinVerifyTrust 结果待 Windows 执行验证，Linux 纯逻辑测试
不替代真机验证。

文件绑定与竞态（TOCTOU）说明：
- ``file_size`` / ``file_mtime_ns`` 只是**常见变化检测**线索：大小与修改时间
  **不是内容身份的密码学证明**，攻击者可以伪造相同大小、相同时间戳替换文件；
  因此签名结果额外绑定 **SHA-256 内容指纹**（:func:`compute_file_sha256`，
  64KB 分块读取）作为内容身份强校验。
- :func:`preflight_launch` 在发起提权启动前，把 realpath 消解符号链接后的
  路径 / 大小 / mtime / SHA-256 与验证快照逐项比对，任一不一致即阻止启动，
  要求重新验证；不提供“继续冒险启动”的旁路。
- 校验与启动之间仍存在理论 TOCTOU 窗口（OS 级残余风险，本模块不引入跨进程
  锁——跨进程锁超出范围）；启动改用 ``os.path.realpath`` 规范化绝对路径，
  收窄临时目录/共享目录/路径替换面。Windows 真机符号链接/交接点行为待
  Windows 执行验证。
"""

from __future__ import annotations

import hashlib
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

    - OK                    : 签名有效（证书链受信、摘要匹配、未过期、吊销已查）。
    - UNSIGNED             : 文件没有任何 Authenticode 签名。
    - INVALID              : 存在签名但无效（被篡改/摘要不符/证书不受信/已吊销/链错误）。
    - NOT_VERIFIABLE       : 无法完成验证（吊销未知/服务器离线/用户取消/系统拒绝）。
    - UNSUPPORTED_EXTENSION: 扩展名不在允许范围（仅 .exe / .msi）。
    """

    OK = "ok"
    UNSIGNED = "unsigned"
    INVALID = "invalid"
    NOT_VERIFIABLE = "not_verifiable"
    UNSUPPORTED_EXTENSION = "unsupported_extension"


# ---------------------------------------------------------------------------
# WinVerifyTrust / 证书 HRESULT 常量（MS Learn: winerror.h / wintrust.h）
# 纯函数 classify_winverifytrust 使用；Windows 分支的 ctypes 调用复用同一批值。
# ---------------------------------------------------------------------------
ERROR_SUCCESS = 0

# 未签名（wintrust.h: TRUST_E_NOSIGNATURE）
TRUST_E_NOSIGNATURE = 0x800B0100

# 明确吊销 / 显式不信任（winerror.h）
CERT_E_REVOKED = 0x800B0105              # 证书被签发者明确吊销
TRUST_E_EXPLICIT_DISTRUST = 0x800B0111   # 证书被显式不信任（策略禁用）

# 签名无效：摘要不符 / 提供方未知 / 主体不受信 / 过期 / 链不可信 / 链错误
TRUST_E_BAD_DIGEST = 0x80096010
TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
TRUST_E_SUBJECT_NOT_TRUSTED = 0x800B0109
CERT_E_EXPIRED = 0x800B0101
CERT_E_UNTRUSTEDROOT = 0x800B010A
CERT_E_CHAINING = 0x800B010B
CERT_E_UNTRUSTEDTESTROOT = 0x800B010D

# 吊销状态未知 / 吊销服务器离线：不得视为有效 → not_verifiable
CERT_E_REVOCATION_FAILURE = 0x800B010E
CRYPT_E_REVOCATION_OFFLINE = 0x80092013

# Win32 系统错误（非 HRESULT 高位）：用户取消 / 系统拒绝 → not_verifiable
ERROR_ACCESS_DENIED = 5
ERROR_CANCELLED = 1223

# 吊销检查策略（wintrust.h fdwRevocationChecks）：
# WTD_REVOKE_NONE=0 不查吊销（已废弃，禁止使用）；WTD_REVOKE_WHOLECHAIN=1
# 对整条链做吊销检查；WTD_REVOKE_CHAIN=2 仅查链。
WTD_REVOKE_NONE = 0
WTD_REVOKE_WHOLECHAIN = 1
WTD_REVOKE_CHAIN = 2

# 明确吊销/显式不信任 → invalid（阻止）
_REVOKED_EXPLICIT = frozenset({CERT_E_REVOKED, TRUST_E_EXPLICIT_DISTRUST})
# 吊销未知/网络不可用 → not_verifiable（阻止，绝不放行）
_REVOCATION_UNKNOWN = frozenset({CERT_E_REVOCATION_FAILURE, CRYPT_E_REVOCATION_OFFLINE})
# 已知签名/链错误 → invalid（阻止）
_INVALID_KNOWN = frozenset({
    TRUST_E_BAD_DIGEST,
    TRUST_E_PROVIDER_UNKNOWN,
    TRUST_E_SUBJECT_NOT_TRUSTED,
    CERT_E_EXPIRED,
    CERT_E_UNTRUSTEDROOT,
    CERT_E_CHAINING,
    CERT_E_UNTRUSTEDTESTROOT,
})
# 用户取消 / 系统拒绝 → not_verifiable（阻止）
_SYSTEM_REFUSED = frozenset({ERROR_ACCESS_DENIED, ERROR_CANCELLED})


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
        publisher: 签名发布者名称（证书 Subject 的简单显示名）；取不到为 None，
            UI 显示“未知”，禁止猜测/编造。
        signer: 签名者/证书主体详细串；取不到为 None。
        timestamp: 签名时间戳（可验证的 RFC3161/Authenticode 时间戳）；未提取为 None。
        failure_reason: 失败/阻止的人类可读原因；ok 时为 None。
        is_msi: 是否为 MSI 安装包（决定启动方式）。
        checked_at: 检查时刻（UTC ISO8601）；纯逻辑构造时可为空串。
        file_size: 验证时刻的文件字节数快照（仅作常见变化检测线索；
                   **不是内容身份的密码学证明**，可被伪造）；未知为 None。
        file_mtime_ns: 验证时刻的 ``st_mtime_ns`` 快照（仅作常见变化检测线索；
                       **不是内容身份的密码学证明**，可被伪造）；未知为 None。
        sha256: 验证时刻计算的 SHA-256 内容指纹（64KB 分块读取，十六进制），
                是绑定到具体文件内容的强身份校验；读取失败/未知为 None。
                非 ok 状态同样尽量携带指纹，供提权前一致性比对。
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
    file_size: int | None = None
    file_mtime_ns: int | None = None
    sha256: str | None = None


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
# 纯逻辑：HRESULT 归一化与分类（ctypes 有符号问题在归一化处解决）
# ---------------------------------------------------------------------------
def normalize_hresult(hr: int) -> int:
    """把 ctypes 返回的有符号 32 位 HRESULT 归一化为无符号 32 位。

    ctypes 按 ``restype`` 解释位模式：``restype=c_long`` 时
    ``c_long(-2146889728).value`` 仍是负数，直接与 ``0x800B0100`` 比较会失配；
    必须先 ``& 0xFFFFFFFF`` 取无符号再比较。正的小整数 Win32 错误码
    （如 5 / 1223）按原样保留。
    """
    return int(hr) & 0xFFFFFFFF


def classify_winverifytrust(hr: int) -> tuple[VerificationStatus, str | None]:
    """纯函数：``WinVerifyTrust`` 返回码 → 验证分类 + 人类可读原因。

    有符号负值经 :func:`normalize_hresult` 归一化后比较，**有符号负值绝不
    被误判为成功**；只有 ``ERROR_SUCCESS(0)`` 归 OK。

    返回 ``(verification, failure_reason)``；OK 时 failure_reason 为 None。
    """
    code = normalize_hresult(hr)

    if code == ERROR_SUCCESS:
        return VerificationStatus.OK, None

    if code == TRUST_E_NOSIGNATURE:
        return VerificationStatus.UNSIGNED, "文件未进行 Authenticode 数字签名。"

    if code in _REVOKED_EXPLICIT:
        return VerificationStatus.INVALID, (
            "证书已被明确吊销或被策略显式不信任，签名无效，已阻止提权。")

    if code in _REVOCATION_UNKNOWN:
        return VerificationStatus.NOT_VERIFIABLE, (
            "吊销状态无法确认（吊销服务器离线/不可达），出于谨慎默认阻止提权。")

    if code in _SYSTEM_REFUSED:
        return VerificationStatus.NOT_VERIFIABLE, (
            "用户取消或系统拒绝了签名验证，出于谨慎默认阻止提权。")

    if code in _INVALID_KNOWN:
        return VerificationStatus.INVALID, (
            "签名无效（摘要不匹配/证书不受信/链错误/已过期），已阻止提权。")

    # 高位家族兜底：0x800Bxxxx 信任/证书错误、0x8009xxxx 加密错误 → invalid；
    # 其余未知码 fail-closed → not_verifiable，绝不放行。
    family = code & 0xFFFF0000
    if family in (0x800B0000, 0x80090000):
        return VerificationStatus.INVALID, (
            "签名无效（证书/摘要校验未通过），已阻止提权。")

    return VerificationStatus.NOT_VERIFIABLE, (
        f"签名验证未返回确定结论（HRESULT 0x{code:08X}），"
        "出于谨慎默认阻止提权。")


# ---------------------------------------------------------------------------
# 纯逻辑：文件快照、SHA-256 内容指纹与签名结果新鲜度（路径绑定 + 内容变更重验）
# ---------------------------------------------------------------------------
# 64KB 分块读取：避免一次性读入大安装包，且分块/一次读结果一致（可单测）。
_FINGERPRINT_CHUNK_SIZE = 64 * 1024


def compute_file_sha256(path: str) -> str | None:
    """计算文件内容的 SHA-256 十六进制指纹（64KB 分块流式读取）。

    这是**内容身份的密码学强校验**：相同内容必然相同指纹，不同内容（即使
    大小、mtime 被伪造得完全一致）指纹必然不同。文件不存在 / 不可读 → None，
    由调用方按失配处理（fail-closed，绝不把 None 当“匹配”）。

    纯函数（仅本地文件 IO），Linux 可直接单测。
    """
    target = str(path or "")
    if not target:
        return None
    try:
        digest = hashlib.sha256()
        with open(target, "rb") as fh:
            for chunk in iter(lambda: fh.read(_FINGERPRINT_CHUNK_SIZE), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def file_snapshot(path: str) -> tuple[int | None, int | None]:
    """读取 ``(st_size, st_mtime_ns)`` 作为签名结果绑定快照。

    注意：大小 / 修改时间**不是内容身份的密码学证明**，只能用于发现常见的
    重写/改动；防“同大小同时间戳替换”必须依赖 :func:`compute_file_sha256`。
    文件不存在 / 不可读 → ``(None, None)``（由调用方按失配处理）。
    """
    try:
        st = os.stat(str(path or ""))
    except OSError:
        return None, None
    return st.st_size, st.st_mtime_ns


def file_identity_snapshot(path: str) -> tuple[int | None, int | None, str | None]:
    """读取 ``(st_size, st_mtime_ns, sha256)`` 完整身份快照。

    大小/mtime 仅作常见变化检测；``sha256`` 才是内容身份强校验。
    文件不存在 / 不可读 → 三元组全部为 None（由调用方按失配处理）。
    """
    target = str(path or "")
    size, mtime_ns = file_snapshot(target)
    if size is None:
        return None, None, None
    return size, mtime_ns, compute_file_sha256(target)


def signature_stale(status: SignatureStatus | None, path: str) -> bool:
    """已记录的签名结果是否与磁盘文件失配（失配必须重新验证，禁止复用旧结果）。

    判定（任一命中即 True）：

    - ``status`` 为 None（尚无结果）；
    - 路径为空，或与结果记录的 ``path`` 不一致（换了文件）；
    - 文件不存在（被删除/移动）；
    - 结果未携带大小/mtime 快照（无法证明未变，保守重验）；
    - 当前大小或 ``mtime_ns`` 与记录快照不一致（内容被改动/重写）；
    - 结果携带 SHA-256 指纹时，当前指纹与记录不一致（**同大小同时间戳的
      内容替换也必然被捕获**）；未携带指纹（旧结果）时回退 size/mtime 判定，
      严格的“缺指纹即重验”由 :func:`preflight_launch` 在启动前执行。

    全部一致才返回 False。纯函数，Linux 可直接单测。
    """
    if status is None:
        return True
    target = str(path or "").strip()
    if not target or status.path != target:
        return True

    size, mtime_ns = file_snapshot(target)
    if size is None:
        return True  # 文件不存在或不可读
    if status.file_size is None or status.file_mtime_ns is None:
        return True  # 旧结果未携带快照，保守重验
    if size != status.file_size or mtime_ns != status.file_mtime_ns:
        return True
    # 密码学内容指纹：携带即强校验（捕获同大小/同时间戳替换）；
    # 未携带（旧结果）回退上面的 size/mtime 判定，保持向后兼容。
    if status.sha256 is not None and compute_file_sha256(target) != status.sha256:
        return True
    return False


# 提权前一致性阻止原因（统一文案，preflight 与 UI 复用）
PREFLIGHT_CHANGED_REASON = "文件内容已变化，需重新验证签名后再启动。"


def preflight_launch(path: str, status: SignatureStatus | None) -> tuple[bool, str | None]:
    """提权启动前的最终一致性确认（独立纯函数，可单测）。

    把 ``path`` 经 ``os.path.realpath`` 消解符号链接后，与验证快照逐项比对：

    1. realpath 路径一致（链接指向同一真实文件才算同一文件；链接被改指他处
       即阻止，避免“验证的是 A，启动的是 B”）；
    2. 文件大小一致；
    3. ``st_mtime_ns`` 一致；
    4. SHA-256 内容指纹一致（密码学强校验，捕获同大小/同时间戳替换）。

    任一不一致 / 缺失 / 文件不可读 / ``status`` 为 None → 返回
    ``(False, reason)`` 阻止启动，调用方必须重新验证；**不提供继续冒险启动
    的旁路**。全部一致 → ``(True, None)``。

    残余风险（如实声明）：本函数与随后的 ``ShellExecuteW`` 之间仍存在理论
    TOCTOU 窗口，属 OS 级残余风险；本模块不引入跨进程锁（超出范围），启动侧
    另以 realpath 规范化绝对路径收窄路径替换面。Windows 真机符号链接/交接点
    行为待 Windows 执行验证。
    """
    if status is None:
        return False, "尚无签名验证结果，无法确认文件内容，需重新验证后再启动。"

    requested = str(path or "").strip()
    if not requested:
        return False, "未提供安装程序路径，需重新验证签名后再启动。"

    # realpath 消解符号链接/相对路径/冗余分隔符后再比较，而非比较用户原始串。
    try:
        requested_real = os.path.realpath(requested)
        recorded_real = os.path.realpath(status.path)
    except OSError:
        return False, PREFLIGHT_CHANGED_REASON

    if requested_real != recorded_real:
        return False, "文件路径已变化（符号链接被改指/文件被移动替换），" + PREFLIGHT_CHANGED_REASON

    size, mtime_ns, digest = file_identity_snapshot(requested_real)
    if size is None:
        return False, "文件不存在或不可读，" + PREFLIGHT_CHANGED_REASON

    # 快照不完整（缺大小/mtime）→ 无法逐项比对，保守阻止重验。
    if status.file_size is None or status.file_mtime_ns is None:
        return False, "签名结果快照不完整，无法确认文件未被替换，需重新验证。"
    if size != status.file_size:
        return False, "文件大小已变化，" + PREFLIGHT_CHANGED_REASON
    if mtime_ns != status.file_mtime_ns:
        return False, "文件修改时间已变化，" + PREFLIGHT_CHANGED_REASON

    # 密码学指纹是内容身份的最终依据：缺失或不一致一律阻止。
    if status.sha256 is None:
        return False, "签名结果未携带内容指纹，无法证明文件未被替换，需重新验证。"
    if digest is None or digest != status.sha256:
        return False, "文件内容指纹（SHA-256）不匹配，" + PREFLIGHT_CHANGED_REASON

    return True, None


# ---------------------------------------------------------------------------
# 平台无关的结果构造助手（绑定路径与文件快照）
# ---------------------------------------------------------------------------
def _build_status(
    path: str,
    *,
    file_exists: bool,
    verification: VerificationStatus,
    failure_reason: str | None = None,
    publisher: str | None = None,
    signer: str | None = None,
    timestamp: str | None = None,
    is_msi: bool = False,
    sha256: str | None = None,
) -> SignatureStatus:
    """统一构造 :class:`SignatureStatus` 并绑定磁盘文件快照（含 SHA-256 指纹）。

    ``sha256`` 显式传入时优先采用（Windows 路径在调用 WinVerifyTrust **之前**
    先算指纹，保证绑定的是被验证那一刻的内容）；否则现场读取完整身份快照。
    """
    if file_exists:
        size, mtime_ns, digest = file_identity_snapshot(path)
        if sha256 is None:
            sha256 = digest
    else:
        size, mtime_ns, sha256 = None, None, None
    return SignatureStatus(
        path=path,
        file_exists=file_exists,
        verification=verification,
        publisher=publisher,
        signer=signer,
        timestamp=timestamp,
        failure_reason=failure_reason,
        is_msi=is_msi,
        checked_at=_now_iso(),
        file_size=size,
        file_mtime_ns=mtime_ns,
        sha256=sha256,
    )


# ---------------------------------------------------------------------------
# Windows 真实验证（条件导入；非 Windows 抛 UNSUPPORTED_PLATFORM，不伪造）
# ---------------------------------------------------------------------------
def verify_authenticode(path: str) -> SignatureStatus:
    """检查 ``path`` 的 Authenticode 签名状态。

    非 Windows 平台直接抛 :class:`PrintError(UNSUPPORTED_PLATFORM)`——
    Linux 沙箱没有 WinVerifyTrust，**禁止伪造验证结果**；纯决策由
    :func:`should_block_elevation` 在 Linux 上独立测试。

    Windows 上通过 ``wintrust.dll!WinVerifyTrust``（通用验证动作，吊销检查
    WTD_REVOKE_WHOLECHAIN）对 EXE / MSI 分别走各自 SIP 完成验证，结果经
    :func:`classify_winverifytrust` 映射到 :class:`VerificationStatus`；
    验证前先计算 SHA-256 内容指纹并随结果绑定（非 ok 状态也尽量携带），
    供 :func:`preflight_launch` 启动前复核。
    """
    target = str(path or "").strip()
    if not target:
        return _build_status(
            "", file_exists=False,
            verification=VerificationStatus.NOT_VERIFIABLE,
            failure_reason="未提供安装程序路径，无法验证签名。",
        )

    if not authenticode_available():
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="Authenticode 签名验证仅支持 Windows；当前平台无法验证，"
                       "出于安全默认阻止提权，请在 Windows 官方环境中操作。",
        )

    if not os.path.isfile(target):
        kind = detect_installer_kind(target)
        return _build_status(
            target,
            file_exists=False,
            verification=(VerificationStatus.UNSUPPORTED_EXTENSION
                          if kind == "unsupported"
                          else VerificationStatus.NOT_VERIFIABLE),
            failure_reason=("文件不存在，无法验证签名。"
                            if kind != "unsupported"
                            else "不支持的安装程序类型（仅 .exe / .msi）。"),
            is_msi=(kind == "msi"),
        )

    kind = detect_installer_kind(target)
    if kind == "unsupported":
        return _build_status(
            target, file_exists=True,
            verification=VerificationStatus.UNSUPPORTED_EXTENSION,
            failure_reason="不支持的安装程序类型（仅 .exe / .msi），已阻止提权。",
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

    生命周期约定：
    - ``dwStateAction = WTD_STATEACTION_VERIFY(1)`` 发起验证；
    - 无论成功/失败，``finally`` 中以 ``WTD_STATEACTION_CLOSE(2)`` 关闭验证
      状态句柄（句柄为 None/0 时跳过），防止 ``hWVTStateData`` 泄漏；
    - ``action`` / ``file_info`` / ``wtd`` 均为局部变量，调用期间保持存活，
      不提前释放；ctypes 结构体指针在调用栈内有效。
    - ``restype=c_long``（有符号），返回值经 :func:`normalize_hresult` 归一化
      为无符号再分类。
    """
    import ctypes
    from ctypes import wintypes

    # 先算内容指纹再调用 WinVerifyTrust：把签名结论绑定到“被验证那一刻”的
    # 具体字节；随后 preflight_launch 用同一指纹在启动前复核，捕获验证后替换。
    pre_verify_digest = compute_file_sha256(path)

    # WINTRUST_ACTION_GENERIC_VERIFY_V2 GUID {00AAC56B-CD44-11d0-8CC2-00C04FC295EE}
    # MS Learn: winverifytrust.h / WintrustAction GUID 常量说明。
    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class _WINTRUST_FILE_INFO(ctypes.Structure):
        # MS Learn: WINTRUST_FILE_INFO 结构（64 位按指针自然对齐）。
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class _WINTRUST_DATA(ctypes.Structure):
        # MS Learn: WINTRUST_DATA 结构；字段顺序与原生布局一致，
        # ctypes 自动按 64 位指针对齐补白。pSignatureSettings 为 Vista+
        # 可选扩展，置 NULL（不设置）即可。
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFileOrCatalogOrBlobOrSgnrOrCert", ctypes.c_void_p),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
        ]

    # wintrust.h 常量
    WTD_UI_NONE = 2
    WTD_CHOICE_FILE = 1
    WTD_STATEACTION_VERIFY = 1
    WTD_STATEACTION_CLOSE = 2

    action = _GUID(0x00AAC56B, 0xCD44, 0x11D0,
                   (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0,
                                        0x4F, 0xC2, 0x95, 0xEE))
    file_info = _WINTRUST_FILE_INFO()
    file_info.cbStruct = ctypes.sizeof(_WINTRUST_FILE_INFO)
    file_info.pcwszFilePath = path
    file_info.hFile = None

    wtd = _WINTRUST_DATA()
    wtd.cbStruct = ctypes.sizeof(_WINTRUST_DATA)
    wtd.dwUIChoice = WTD_UI_NONE
    # 开启整条链吊销检查（禁止回退 WTD_REVOKE_NONE）。
    wtd.fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN
    wtd.dwUnionChoice = WTD_CHOICE_FILE
    wtd.pFileOrCatalogOrBlobOrSgnrOrCert = ctypes.cast(
        ctypes.pointer(file_info), ctypes.c_void_p)
    wtd.dwStateAction = WTD_STATEACTION_VERIFY

    wintrust = ctypes.windll.wintrust
    # restype 显式声明为有符号 c_long，返回值统一走 normalize_hresult。
    wintrust.WinVerifyTrust.restype = ctypes.c_long
    wintrust.WinVerifyTrust.argtypes = [
        wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(_WINTRUST_DATA),
    ]

    try:
        try:
            hr_signed = wintrust.WinVerifyTrust(
                None, ctypes.byref(action), ctypes.byref(wtd))
        except OSError as exc:
            return _build_status(
                path, file_exists=True,
                verification=VerificationStatus.NOT_VERIFIABLE,
                failure_reason=f"签名验证调用被系统拒绝（{exc}），已默认阻止提权。",
                is_msi=is_msi,
                sha256=pre_verify_digest,
            )

        verification, reason = classify_winverifytrust(hr_signed)

        # 仅在签名有效时最佳努力提取发布者；失败 → publisher=None（不编造）。
        publisher = signer = timestamp = None
        if verification is VerificationStatus.OK:
            publisher, signer, timestamp = _best_effort_signer(
                path, is_msi=is_msi)

        return _build_status(
            path, file_exists=True,
            verification=verification,
            failure_reason=reason,
            publisher=publisher, signer=signer, timestamp=timestamp,
            is_msi=is_msi,
            sha256=pre_verify_digest,
        )
    finally:
        # 无论验证成功或失败，都关闭验证状态句柄；None/0 跳过。
        if wtd.hWVTStateData:
            wtd.dwStateAction = WTD_STATEACTION_CLOSE
            try:
                wintrust.WinVerifyTrust(
                    None, ctypes.byref(action), ctypes.byref(wtd))
            except OSError:
                pass


# ---------------------------------------------------------------------------
# 签名者/发布者最佳努力提取（provider 可注入，Linux 用 fake provider 单测）
# ---------------------------------------------------------------------------
def _extract_signer_info(provider, path: str) -> tuple[str | None, str | None, str | None]:
    """经注入的 crypto provider 最佳努力提取发布者显示名。

    ``provider`` 需实现：
    - ``query(path) -> (store, cert_ctx, owns_cert) | None``：
      CryptQueryObject 结果；无证书上下文返回 None。
    - ``enum_first(store) -> cert_ctx | None``：从证书库取第一个证书上下文。
    - ``display_name(cert_ctx) -> str | None``：CertGetNameStringW 简单显示名。
    - ``free_cert(cert_ctx)``：CertFreeCertificateContext（仅自有上下文）。
    - ``close_store(store)``：CertCloseStore。

    任何一步失败都返回 ``(None, None, None)``，**禁止编造发布者**；
    时间戳在本实现中不提取，恒为 None（UI 不显示伪造时间戳）。
    """
    try:
        queried = provider.query(path)
    except OSError:
        return None, None, None
    if not queried:
        return None, None, None

    store, cert_ctx, owns_cert = queried
    try:
        if not cert_ctx and store is not None:
            cert_ctx = provider.enum_first(store)
            owns_cert = False
        if not cert_ctx:
            return None, None, None
        name = provider.display_name(cert_ctx)
        if not name or not str(name).strip():
            return None, None, None
        clean = str(name).strip()
        return clean, clean, None
    finally:
        if cert_ctx and owns_cert:
            provider.free_cert(cert_ctx)
        if store is not None:
            provider.close_store(store)


# pragma: no cover - Windows 真机最佳努力取发布者（取不到则留 None，不编造）
def _best_effort_signer(path: str, *, is_msi: bool) -> tuple[str | None, str | None, str | None]:
    """用 crypt32 ``CryptQueryObject`` 取第一个证书上下文，再经
    ``CertGetNameStringW(CERT_NAME_SIMPLE_DISPLAY_TYPE)`` 得发布者显示名。

    防御性：指针空检查、``CertFreeCertificateContext`` 释放自有上下文、
    ``CertCloseStore`` 关闭库；提取失败一律返回 None（UI 显示“未知”）。
    MSI 亦可走 ``msi.dll!MsiGetFileSignatureInformationW``，作为可选增强点保留，
    默认以 CryptQueryObject 结果为准。**时间戳未提取，如实为 None。**
    """
    import ctypes
    from ctypes import wintypes

    # crypt32.h / wincrypt.h 常量
    CERT_QUERY_OBJECT_FILE = 1
    CERT_QUERY_CONTENT_FLAG_ALL = 0x0000FFFF
    CERT_QUERY_FORMAT_FLAG_ALL = 0x0000000F
    CERT_NAME_SIMPLE_DISPLAY_TYPE = 0

    crypt32 = ctypes.windll.crypt32

    class _CERT_CONTEXT(ctypes.Structure):
        # 不透明结构体：这里只持有指针，字段不访问。
        _fields_ = [("dwCertEncodingType", wintypes.DWORD),
                    ("pbCertEncoded", ctypes.c_void_p),
                    ("cbCertEncoded", wintypes.DWORD),
                    ("pCertInfo", ctypes.c_void_p),
                    ("hCertStore", wintypes.HANDLE)]

    crypt32.CryptQueryObjectW.restype = wintypes.BOOL
    crypt32.CryptQueryObjectW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    crypt32.CertEnumCertificatesInStore.restype = ctypes.POINTER(_CERT_CONTEXT)
    crypt32.CertEnumCertificatesInStore.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(_CERT_CONTEXT)]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.POINTER(_CERT_CONTEXT), wintypes.DWORD, ctypes.c_void_p,
        wintypes.LPCWSTR, wintypes.DWORD]
    crypt32.CertFreeCertificateContext.restype = wintypes.BOOL
    crypt32.CertFreeCertificateContext.argtypes = [
        ctypes.POINTER(_CERT_CONTEXT)]
    crypt32.CertCloseStore.restype = wintypes.BOOL
    crypt32.CertCloseStore.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    class _Provider:
        def __init__(self):
            self._h_store = wintypes.HANDLE()
            self._pv_context = ctypes.c_void_p()
            self._dw_content = wintypes.DWORD()

        def query(self, file_path):
            ok = crypt32.CryptQueryObjectW(
                CERT_QUERY_OBJECT_FILE, file_path,
                CERT_QUERY_CONTENT_FLAG_ALL, CERT_QUERY_FORMAT_FLAG_ALL, 0,
                None, ctypes.byref(self._dw_content), None,
                ctypes.byref(self._h_store), ctypes.byref(self._pv_context), None)
            if not ok:
                return None
            # 仅当内容类型本身就是证书上下文时，pv_context 才可直接当证书用；
            # 否则交给证书库枚举（PKCS7 嵌入签名在此可能取不到 → None，诚实返回）。
            cert_raw = self._pv_context.value
            store = self._h_store.value or None
            cert_ctx = ctypes.cast(cert_raw, ctypes.POINTER(_CERT_CONTEXT)) \
                if cert_raw else None
            return (store, cert_ctx, bool(cert_raw))

        def enum_first(self, store):
            ctx = crypt32.CertEnumCertificatesInStore(store, None)
            return ctx if ctx else None

        def display_name(self, cert_ctx):
            size = crypt32.CertGetNameStringW(
                cert_ctx, CERT_NAME_SIMPLE_DISPLAY_TYPE, None, None, 0)
            if size <= 1:
                return None
            buf = ctypes.create_unicode_buffer(size)
            crypt32.CertGetNameStringW(
                cert_ctx, CERT_NAME_SIMPLE_DISPLAY_TYPE, None, buf, size)
            return buf.value

        def free_cert(self, cert_ctx):
            crypt32.CertFreeCertificateContext(cert_ctx)

        def close_store(self, store):
            crypt32.CertCloseStore(store, 0)

    return _extract_signer_info(_Provider(), path)


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
        "文件签名无效（证书不受信/摘要不匹配/已吊销/文件可能被篡改），"
        "已默认阻止提权，请勿运行该文件。"
    ),
    VerificationStatus.NOT_VERIFIABLE: (
        "无法完成签名验证（吊销未知/服务器离线/用户取消/系统拒绝/当前环境"
        "不可用），出于谨慎已默认阻止提权；请改从厂商官方渠道获取安装包后重试。"
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
    | invalid                 | 是    | 签名无效（含明确吊销），默认阻止 |
    | not_verifiable          | 是    | 无法验证（含吊销未知/离线），谨慎阻止 |
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
    """汇总签名状态/发布者/文件路径/失败原因，供 UI 提权前展示。

    发布者提取失败时显示“未知”，绝不猜测或编造名称。
    """
    lines = [f"签名状态：{signature_status_label(status)}"]
    publisher = status.publisher or "未知（未提取到发布者，请核对官方来源）"
    lines.append(f"发布者：{publisher}")
    lines.append(f"文件路径：{status.path}")
    if status.sha256:
        # 仅展示短前缀供人工比对，完整 64 位指纹保存在状态对象内供机器复核。
        lines.append(f"内容指纹：已绑定 SHA-256（{status.sha256[:12]}…）")
    if status.timestamp:
        lines.append(f"签名时间戳：{status.timestamp}")
    if status.failure_reason:
        lines.append(f"说明：{status.failure_reason}")
    return "\n".join(lines)


def elevation_blocked_message(status: SignatureStatus) -> str:
    """阻止提权时给用户的完整文案（含状态与原因）。"""
    _, reason = should_block_elevation(status)
    return f"已阻止提权（{signature_status_label(status)}）：{reason}"
