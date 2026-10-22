"""打印机驱动检测与安装指引（guidance only）。

本模块的边界（安全红线，全项目统一）：

- **绝不捆绑驱动二进制**：安装包与应用内不携带任何厂商驱动文件。
- **绝不自动下载、自动安装驱动**：只展示官方来源与操作指引，
  驱动安装由用户从厂商官方渠道自行下载、自行以管理员身份运行。
- **绝不调用 ``bcdedit``、绝不禁用驱动签名强制（DSE）**、
  **绝不使用 ``pnputil /add-driver`` 安装未签名驱动**、
  **不修改组策略或任何 Windows 安全设置**。
- ``elevate_process`` 仅用于“启动用户自己从官方渠道下载好的驱动安装程序”，
  提权前先做 Authenticode 签名验证：未签名/无效/无法验证/类型不支持**默认阻止
  提权**，由 Windows UAC 自身完成提权授权，应用不绕过、不关闭任何安全校验，
  不静默提权。签名有效也不代表文件绝对安全，UI 必须提示核对官方来源。

纯逻辑（状态决策、签名闸门、命令计划、指引文案、官方来源表）在任意平台可
导入、可测试；只有 Windows 专属的 ``detect_driver_state_windows`` /
``elevate_process`` 在非 Windows 上抛出 ``PrintError(UNSUPPORTED_PLATFORM)``。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum

from .authenticode import (  # noqa: F401  (纯逻辑再导出，供调用方/测试统一入口)
    SIGNED_OK_CAUTION,
    SignatureStatus,
    VerificationStatus,
    build_shellexec_parameters,
    describe_signature_status,
    elevation_blocked_message,
    map_elevation_outcome,
    parse_elevate_request,
    should_block_elevation,
    verify_authenticode,
)
from .errors import PrintError


# ---------------------------------------------------------------------------
# 状态枚举
# ---------------------------------------------------------------------------
class DriverState(str, Enum):
    """驱动/队列/设备综合状态。

    优先级（detect_driver_state 由高到低判定）：
    1. 打印机名为空            → UNKNOWN（无法判断）
    2. 无队列（has_queue=False）→ QUEUE_MISSING
    3. 设备离线（is_offline）  → DEVICE_OFFLINE
    4. 上次安装失败标记         → DRIVER_INSTALL_FAILED
    5. 队列存在但驱动名为空     → DRIVER_MISSING
    6. 其余                     → DRIVER_OK
    """

    UNKNOWN = "unknown"
    QUEUE_MISSING = "queue_missing"
    DRIVER_OK = "driver_ok"
    DRIVER_MISSING = "driver_missing"
    DRIVER_INSTALL_FAILED = "driver_install_failed"
    DEVICE_OFFLINE = "device_offline"


# ---------------------------------------------------------------------------
# 纯函数：状态决策（Linux 可测）
# ---------------------------------------------------------------------------
def detect_driver_state(
    printer_name: str,
    has_queue: bool,
    driver_name: str | None,
    is_offline: bool,
    install_failed_flag: bool = False,
) -> DriverState:
    """按固定优先级把原始观测值决策为 :class:`DriverState`。

    判定规则表（优先级从上到下，先命中先返回）：

    | 条件                                              | 结果                  |
    |---------------------------------------------------|-----------------------|
    | ``printer_name`` 为空（去空白后）                 | UNKNOWN               |
    | ``has_queue=False``（系统里找不到该队列）          | QUEUE_MISSING         |
    | ``is_offline=True``（设备报告离线/错误连接）       | DEVICE_OFFLINE        |
    | ``install_failed_flag=True``（上次安装失败被标记）  | DRIVER_INSTALL_FAILED  |
    | ``driver_name`` 为空（队列存在但无驱动绑定）       | DRIVER_MISSING        |
    | 其余（队列在、驱动在、在线、无失败标记）           | DRIVER_OK             |

    参数:
        printer_name: 目标打印机队列名；空串表示尚无目标。
        has_queue: 该队列在系统中是否存在。
        driver_name: 队列绑定的驱动名（win32print ``pDriverName``），可为 None。
        is_offline: 设备是否报告离线。
        install_failed_flag: 调用方持有的“上次驱动安装失败”标记（由 UI 持久化）。

    纯函数，不触碰系统 API。
    """
    if not printer_name or not str(printer_name).strip():
        return DriverState.UNKNOWN
    if not has_queue:
        return DriverState.QUEUE_MISSING
    if is_offline:
        return DriverState.DEVICE_OFFLINE
    if install_failed_flag:
        return DriverState.DRIVER_INSTALL_FAILED
    if not driver_name or not str(driver_name).strip():
        return DriverState.DRIVER_MISSING
    return DriverState.DRIVER_OK


# ---------------------------------------------------------------------------
# Windows 检测（惰性 import，非 Windows 抛错）
# ---------------------------------------------------------------------------
def detect_driver_state_windows(printer_name: str) -> DriverState:
    """在 Windows 上调用冻结接口 ``windows_print`` 检测目标队列状态。

    惰性 import ``client.printing.windows_print``（A 切片并行实现，签名冻结）：
    - ``list_printers() -> list[PrinterCapabilities]``
    - ``get_driver_info(printer_name) -> DriverInfo``

    非 Windows 环境直接抛 :class:`PrintError(UNSUPPORTED_PLATFORM)`，
    不触发任何 win32 API。
    """
    if sys.platform != "win32":
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="驱动自动检测仅支持 Windows，其他平台请手动检查打印队列。",
        )

    # 惰性导入：保证本模块在 Linux/CI 可被导入与单测
    from . import windows_print  # type: ignore[import-not-found]

    caps = None
    for item in windows_print.list_printers():
        if item.printer_name == printer_name:
            caps = item
            break

    if caps is None:
        return DriverState.QUEUE_MISSING

    info = windows_print.get_driver_info(printer_name)
    offline = bool(caps.status and caps.status.offline)
    return detect_driver_state(
        printer_name=printer_name,
        has_queue=True,
        driver_name=info.driver_name,
        is_offline=offline,
    )


# ---------------------------------------------------------------------------
# 官方驱动来源表（guidance only，URL 均为厂商官方支持/下载页）
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DriverSource:
    """单个厂商的官方驱动来源条目（只给链接与说明，不携带二进制）。

    Attributes:
        manufacturer: 规范厂商名（用于查表匹配）。
        aliases: 匹配别名（中文名、常见拼写），大小写不敏感。
        official_url: 官方支持/驱动下载页；**禁止填写第三方镜像或网盘**。
        notes: 兼容性说明（适用系统、获取方式提示）。
        verified_signed: 是否已知提供 WHQL/数字签名驱动；None=未核实，不得谎称。
    """

    manufacturer: str
    official_url: str
    notes: str
    verified_signed: bool | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)


# 来源核对（2026-10 核查，均为厂商官网；安装前仍需用户在页面上按型号二次核对）：
# - Zebra:        support.zebra.com 官方 Support Center（Drivers and Downloads，
#                 按型号进入产品页 → Downloads → Driver）。
# - TSC:          tscprinters.com 全球官网 Downloads（官方提供 WHQL 认证 Windows 驱动）。
# - GoDEX 科诚:   godexintl.com/downloads 官方下载中心（按产品/文件类型筛 Driver）。
# - Brother:      support.brother.com Brother Solutions Center（按型号搜驱动）。
# - Honeywell:    automation.honeywell.com 打印机产品支持页（原 honeywellaidc.com
#                 已并入 Honeywell Automation，提供 InterDriver Windows 驱动）。
# - Star Micronics: starmicronics.com Global Support Center（按产品页下载
#                 Star Windows Software / JavaPOS 驱动）。
# - Epson:        epson.com 官方 Support（LabelWorks/ColorWorks 按型号取驱动；
#                 中国大陆用户可用 epson.com.cn 服务与支持）。
# - SATO:         sato-global.com/drivers/redirect.html 官方驱动跳转入口
#                 （官方手册指引的统一驱动下载页）。
# - HPRT 汉印:    cn.hprt.com 官网“下载中心 → 驱动下载”，按型号检索。
# - Gprinter 佳博: gainscha.com 官网，按系列（17/18/19/20/21/CH/CL/M/60 系列）
#                 进入对应支持页取官方驱动。
# - Xprinter 芯烨: xprinter.net 官网“驱动下载”频道，按 80mm/58mm/系列检索。
OFFICIAL_DRIVER_SOURCES: tuple[DriverSource, ...] = (
    DriverSource(
        manufacturer="Zebra",
        official_url="https://support.zebra.com/",
        notes="Zebra 官方 Support Center：进入产品支持页 → Downloads → Driver，"
              "按具体型号（如 ZD420、ZT230）下载对应 ZDesigner Windows 驱动。",
        verified_signed=None,
        aliases=("斑马", "zebra technologies", "zdesigner"),
    ),
    DriverSource(
        manufacturer="TSC",
        official_url="https://www.tscprinters.com/global/en/downloads",
        notes="TSC 官方 Downloads：提供 WHQL 认证 Windows 驱动（Win10/11 32/64 位），"
              "按型号（TDP、TE、TA、TX 等系列）下载。",
        verified_signed=True,
        aliases=("tsc auto", "台半"),
    ),
    DriverSource(
        manufacturer="Godex",
        official_url="https://www.godexintl.com/downloads",
        notes="GoDEX 科诚官方下载中心：按产品/文件类型筛选 Driver，"
              "Windows 驱动覆盖 G500、RT、EZ、DT 等系列。",
        verified_signed=None,
        aliases=("科诚", "godexintl", "gdxprinter"),
    ),
    DriverSource(
        manufacturer="Brother",
        official_url="https://support.brother.com/",
        notes="Brother Solutions Center：输入完整型号（如 TD-4550DNWB、QL-1110NWB）"
              "进入支持页下载对应 Windows 驱动。",
        verified_signed=None,
        aliases=("兄弟", "brother industries"),
    ),
    DriverSource(
        manufacturer="Honeywell",
        official_url="https://automation.honeywell.com/us/en/products/productivity-solutions/printers",
        notes="Honeywell 打印机产品支持页：找对应型号（PC、PM、PX、XD 等系列）"
              "下载 InterDriver Windows 驱动；旧 honeywellaidc.com 已并入该站点。",
        verified_signed=None,
        aliases=("霍尼韦尔", "honeywell aidc", "interdriver"),
    ),
    DriverSource(
        manufacturer="Star Micronics",
        official_url="https://starmicronics.com/support/",
        notes="Star 全球支持中心：按产品（mC-Label、TSP、mPOP 等）进入支持页下载"
              "Star Windows Software / JavaPOS 驱动。",
        verified_signed=None,
        aliases=("star", "星光", "star micronics"),
    ),
    DriverSource(
        manufacturer="Epson",
        official_url="https://epson.com/Support/Printers",
        notes="Epson 官方支持：LabelWorks / ColorWorks 按型号下载驱动；"
              "中国大陆用户请改用 epson.com.cn 服务与支持按型号检索。",
        verified_signed=None,
        aliases=("爱普生", "epson labelworks", "colorworks"),
    ),
    DriverSource(
        manufacturer="SATO",
        official_url="https://www.sato-global.com/drivers/redirect.html",
        notes="SATO 官方驱动统一跳转入口（官方手册指引）：按地区/型号进入下载页，"
              "Windows Gallery 驱动与 All-In-One Tool 分开提供。",
        verified_signed=None,
        aliases=("佐藤", "sato global"),
    ),
    DriverSource(
        manufacturer="HPRT",
        official_url="https://cn.hprt.com/",
        notes="汉印官网：顶部“下载中心 → 驱动下载”，在检索框输入完整型号"
              "（如 FT、N、D、X 系列）下载 Windows 版驱动。",
        verified_signed=None,
        aliases=("汉印", "hprt cloud"),
    ),
    DriverSource(
        manufacturer="Gprinter",
        official_url="https://www.gainscha.com/",
        notes="佳博 Gainscha 官网：按条码机系列（17/18/19/20/21、CH、CL、M、60 系列）"
              "进入对应支持资料页获取官方 Windows 驱动。",
        verified_signed=None,
        aliases=("佳博", "gainscha", "gprinter"),
    ),
    DriverSource(
        manufacturer="Xprinter",
        official_url="https://www.xprinter.net/Driver_Download/p-0-12.html",
        notes="芯烨官网“驱动下载”频道：按 58mm/80mm 热敏与条码系列检索对应 Windows 驱动。",
        verified_signed=None,
        aliases=("芯烨", "xinye"),
    ),
)


def _normalize(name: str) -> str:
    return " ".join(str(name).strip().lower().split())


def _lookup_source(manufacturer: str) -> DriverSource | None:
    """大小写/别名不敏感地查官方来源表；未命中返回 None（绝不编造 URL）。"""
    key = _normalize(manufacturer)
    if not key:
        return None
    for src in OFFICIAL_DRIVER_SOURCES:
        candidates = {_normalize(src.manufacturer), *( _normalize(a) for a in src.aliases)}
        if key in candidates:
            return src
    # 宽松包含匹配（如 "Zebra Technologies ZD420" 含 "zebra"）
    for src in OFFICIAL_DRIVER_SOURCES:
        candidates = [_normalize(src.manufacturer),
                      *(_normalize(a) for a in src.aliases)]
        if any(c and c in key for c in candidates):
            return src
    return None


@dataclass(frozen=True)
class DriverGuidance:
    """给用户的驱动安装指引（命中官方表或回退通用指引）。

    Attributes:
        manufacturer: 用户提供的厂商名（原文回显）。
        model: 设备型号（原文回显，仅用于提示用户在官网按型号检索）。
        matched: 是否命中官方来源表。
        official_url: 官方下载页；未命中时恒为 None（禁止编造 URL）。
        notes: 兼容性/获取说明。
        verified_signed: 已知签名状态，None=未核实。
        generic_hint: 未命中时的通用指引文案。
    """

    manufacturer: str
    model: str
    matched: bool
    official_url: str | None = None
    notes: str = ""
    verified_signed: bool | None = None
    generic_hint: str = ""


def build_driver_guidance(manufacturer: str, model: str) -> DriverGuidance:
    """按厂商名构建驱动指引；命中官方表→给官方 URL，未命中→通用指引且 URL=None。

    诚信约束：未命中时 ``official_url`` 必须为 None，函数不会拼接、猜测任何 URL。
    """
    src = _lookup_source(manufacturer)
    if src is not None:
        return DriverGuidance(
            manufacturer=manufacturer.strip(),
            model=model.strip(),
            matched=True,
            official_url=src.official_url,
            notes=src.notes,
            verified_signed=src.verified_signed,
        )
    return DriverGuidance(
        manufacturer=manufacturer.strip(),
        model=model.strip(),
        matched=False,
        official_url=None,
        notes="",
        verified_signed=None,
        generic_hint=(
            "未在预置官方来源表中找到该厂商。请自行打开该厂商官方网站的"
            "“支持/服务与支持/下载中心”页面，按完整型号搜索并下载官方 Windows "
            "驱动；不要使用第三方驱动下载站或来路不明的安装包。"
        ),
    )


# ---------------------------------------------------------------------------
# UAC 提权辅助（仅 Windows；非 Windows 抛 UNSUPPORTED_PLATFORM）
# ---------------------------------------------------------------------------
class ElevationResult(str, Enum):
    """``elevate_process`` 的提权结果。

    - LAUNCHED          : ShellExecuteW 成功发起（不代表安装成功）。
    - REQUIRES_UAC      : 需要管理员/UAC 授权但当前未获得。
    - CANCELLED_BY_USER : 用户在 UAC 弹窗点了“否”（驱动未安装，不视为成功）。
    - FAILED            : 启动失败（附系统错误码，见 ElevationOutcome.error_code）。
    - BLOCKED           : 签名闸门拦截（未签名/无效/无法验证/类型不支持），
                          未发起任何提权；原因见 ``ElevationOutcome.blocked_reason``。
    """

    LAUNCHED = "launched"
    REQUIRES_UAC = "requires_uac"
    CANCELLED_BY_USER = "cancelled_by_user"
    FAILED = "failed"
    BLOCKED = "blocked"


# ShellExecuteW 纯字符串结果 → ElevationResult（与 authenticode.map_elevation_outcome 对齐）
_KIND_TO_RESULT = {
    "launched": ElevationResult.LAUNCHED,
    "requires_uac": ElevationResult.REQUIRES_UAC,
    "cancelled_by_user": ElevationResult.CANCELLED_BY_USER,
    "failed": ElevationResult.FAILED,
}


@dataclass(frozen=True)
class ElevationOutcome:
    """提权结果 + 附加系统错误码（ShellExecuteW 返回值，<=32 为错误）。

    ``blocked_reason`` 仅在 ``status == BLOCKED`` 时非空，记录签名闸门拦截原因。
    """

    status: ElevationResult
    error_code: int | None = None
    blocked_reason: str | None = None


def elevate_process(
    path: str,
    args: list[str] | tuple[str, ...] | None = None,
    *,
    skip_signature_check: bool = False,
    allow_unsigned: bool = False,
) -> ElevationOutcome:
    """以 ``runas``（触发 Windows UAC）启动用户自备的官方驱动安装程序。

    提权前闸门（默认流程，不可被普通参数关闭）：

    1. ``verify_authenticode(path)`` 检查 Authenticode 签名；
    2. ``should_block_elevation`` 决策：未签名 / 签名无效 / 无法验证 /
       类型不支持 → 返回 ``BLOCKED``，**不发起任何提权**；
    3. 签名 ``ok`` 才进入 UAC 提权；放行时仍携带
       “签名有效≠文件绝对安全，请核对官方来源”的提示（由 UI 展示）。

    安全约束（硬编码，不可被参数关闭）：

    - **只用于启动用户自己从官方渠道下载好的驱动安装程序**；
      本函数不下载、不释放、不生成任何安装包。
    - EXE 与 MSI 分路径启动：EXE 直接以列表参数启动；MSI 经
      ``msiexec.exe /i <path>`` 可见安装界面启动，**绝不使用 /qn 静默**；
      参数一律列表传递、逐令牌引用，不做 shell 字符串拼接，无命令注入面。
    - ``skip_signature_check=True`` 仅用于显式测试/高级排障，**UI 不暴露该
      开关**；即便跳过，扩展名白名单闸门仍然生效，且不改变 UAC 授权流程。
    - ``allow_unsigned`` 仅为兼容历史调用签名保留，**不再、也绝不构成跳过
      签名校验的旁路**（传入 True 同样被签名闸门拦截）。
    - **绝不**调用 ``bcdedit``、**绝不**禁用驱动签名强制（DSE）、
      **绝不**执行 ``pnputil /add-driver`` 安装未签名驱动、
      **不修改组策略/注册表安全项/任何 Windows 安全设置**。
    - 提权授权完全交给 Windows UAC 弹窗：用户拒绝则返回 CANCELLED_BY_USER，
      应用不自行提权、不静默提权。

    非 Windows 平台抛 :class:`PrintError(UNSUPPORTED_PLATFORM)`（守卫在最前，
    即使 ``skip_signature_check=True`` 也不绕过平台守卫）。
    """
    if sys.platform != "win32":
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="UAC 提权仅存在于 Windows，本平台不可用。",
        )

    target = str(path or "").strip()
    if not target:
        return ElevationOutcome(
            ElevationResult.FAILED, error_code=0,
            blocked_reason="未提供安装程序路径。",
        )

    # 历史参数名保留：它不是、也永远不会是跳过签名的旁路。
    _ = allow_unsigned

    # 扩展名白名单闸门：无论是否跳过签名检查，非 .exe/.msi 一律阻止。
    plan = parse_elevate_request(target, args)
    if not plan.supported:
        status = SignatureStatus(
            path=target, file_exists=False,
            verification=VerificationStatus.UNSUPPORTED_EXTENSION,
            failure_reason="仅支持 .exe / .msi 安装程序。",
        )
        return ElevationOutcome(
            ElevationResult.BLOCKED,
            blocked_reason=elevation_blocked_message(status),
        )

    # 签名闸门（默认开启；skip_signature_check 仅显式测试/高级排障使用）。
    if not skip_signature_check:
        status = verify_authenticode(target)
        blocked, reason = should_block_elevation(status)
        if blocked:
            return ElevationOutcome(
                ElevationResult.BLOCKED, blocked_reason=reason,
            )

    # Windows 真实提权（ShellExecuteW runas；EXE/MSI 分路径，列表参数）。
    # pragma: no cover - 仅 Windows 真机执行，Linux 测试不可达
    import ctypes  # 惰性导入，Linux 不触发

    if plan.is_msi:
        lp_file = "msiexec.exe"
        lp_parameters = build_shellexec_parameters(plan)
    else:
        lp_file = plan.path
        lp_parameters = build_shellexec_parameters(plan)

    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", lp_file, lp_parameters, None, 1  # SW_SHOWNORMAL
    )

    kind = map_elevation_outcome(ret)
    result = _KIND_TO_RESULT[kind]
    if result is ElevationResult.LAUNCHED:
        # 发起成功不等于安装成功；附带官方来源提示由 UI 展示。
        return ElevationOutcome(ElevationResult.LAUNCHED, error_code=None)
    if result is ElevationResult.CANCELLED_BY_USER:
        return ElevationOutcome(
            ElevationResult.CANCELLED_BY_USER, error_code=ret)
    if result is ElevationResult.REQUIRES_UAC:
        return ElevationOutcome(ElevationResult.REQUIRES_UAC, error_code=ret)
    return ElevationOutcome(ElevationResult.FAILED, error_code=ret)


# ---------------------------------------------------------------------------
# 状态 → 用户指引文案（关键词由测试断言，勿删）
# ---------------------------------------------------------------------------
def install_guidance_for_state(state: DriverState) -> str:
    """把 :class:`DriverState` 映射为面向用户的中文操作提示。

    文案必须包含对应动作关键词（测试断言）：
    - DRIVER_OK           → 驱动就绪 / 标准测试页 / 校准
    - DRIVER_MISSING      → 官方下载 / 以管理员身份运行 / UAC 确认
    - DRIVER_INSTALL_FAILED → 卸载 / 重启 / 官方源重装 / 检查打印队列
    - DEVICE_OFFLINE      → 电源 / 连接 / 线缆 / 解除队列暂停 / Windows 疑难解答
    """
    if state is DriverState.DRIVER_OK:
        return (
            "驱动就绪：队列已绑定可用驱动。建议先打印一张标准测试页做校准，"
            "确认标签尺寸、浓度与走纸方向正确后再正式打印。"
        )
    if state is DriverState.DRIVER_MISSING:
        return (
            "驱动缺失：请前往厂商官方支持页按型号官方下载 Windows 驱动安装程序，"
            "右键选择“以管理员身份运行”官方安装程序，并在弹出的 UAC 确认框中点“是”；"
            "安装完成后回到本程序重新检测。"
        )
    if state is DriverState.DRIVER_INSTALL_FAILED:
        return (
            "上次驱动安装失败：先在 Windows“设备管理器/打印机”中卸载残留驱动，"
            "重启电脑后从官方源重新下载并重装驱动，安装完成后检查打印队列是否恢复。"
        )
    if state is DriverState.DEVICE_OFFLINE:
        return (
            "设备离线：检查打印机电源、连接方式与线缆（USB/网络），"
            "在打印队列中解除队列暂停，必要时运行 Windows 打印机疑难解答后重试。"
        )
    if state is DriverState.QUEUE_MISSING:
        return (
            "未找到打印队列：先在 Windows“设置 → 蓝牙和设备 → 打印机”中确认设备已连接，"
            "再从厂商官方渠道安装驱动并建立队列，然后回到本程序检测。"
        )
    return (
        "驱动状态未知：请先连接设备并在 Windows 打印机设置中确认队列存在，"
        "再从厂商官方渠道获取对应型号驱动。"
    )
