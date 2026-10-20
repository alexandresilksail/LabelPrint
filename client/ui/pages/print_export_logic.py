"""打印中心纯决策逻辑（不依赖 Qt，Linux 可测）。

本模块把 UI 控件与打印后端之间的决策逻辑抽成无副作用纯函数：

- :func:`merge_capabilities`        合并 win32print 与 Qt 两路枚举结果（去重、
                                    字段互补），返回默认/首台代表；
- :func:`merge_capabilities_list`   返回合并后的完整列表（下拉框用）；
- :func:`pick_paper`                在能力表内查找模板尺寸，未命中回退自定义纸；
- :func:`build_job_from_form`      从表单字典构造 :class:`LabelJobSpec`，
                                    负责偏移应用、份数/质量/方向归一化与
                                    模块宽度/静区默认值；
- :func:`summarize_capabilities`    一行能力摘要（驱动/端口/默认/状态）；
- :func:`group_issues`              把校验问题分为 error / warning 两组。

诚信约束：本模块不伪造 ``verified_with_hardware``，不接触系统 API。
"""

from __future__ import annotations

import dataclasses

from ...printing.calibration import DEFAULT_QUIET_MODULES
from ...printing.capabilities import (
    DriverInfo,
    JobOrientation,
    PaperSize,
    PrintQuality,
    PrinterCapabilities,
    Protocol,
    Transport,
)
from ...printing.adapters.registry import protocol_selection_basis
from ...printing.config_store import MAX_OFFSET_MM
from ...printing.errors import PrintError
from ...printing.job import LabelContent, LabelJobSpec, ValidationIssue
from ...printing.templates import GS1_MODULE_MM

#: 默认静区模块数（与 PDF 渲染 DEFAULT_QUIET_MODULES 一致）
DEFAULT_QUIET_MODULES = 11

#: 打印方式（协议/通道）选项键。协议只能由用户显式选择，禁止按品牌推断。
MODE_UNIVERSAL = "universal"      # 通用 Windows 队列（驱动 + 系统队列）
MODE_ZPL = "zpl"                 # ZPL 专用协议
MODE_TSPL = "tspl"               # TSPL 专用协议
MODE_CPCL = "cpcl"               # CPCL 专用协议
MODE_TCP9100 = "tcp9100"         # 网络 TCP 9100 raw 直连（配合专用协议）

MODE_LABELS = {
    MODE_UNIVERSAL: "通用 Windows 队列（驱动 + 系统队列）",
    MODE_ZPL: "ZPL 专用协议",
    MODE_TSPL: "TSPL 专用协议",
    MODE_CPCL: "CPCL 专用协议",
    MODE_TCP9100: "网络 TCP 9100 直连（raw，无设备确认回执）",
}

#: 专用协议模式集合
PROTOCOL_MODES = (MODE_ZPL, MODE_TSPL, MODE_CPCL)

#: 纸张尺寸匹配容差（mm）
PAPER_MATCH_TOLERANCE_MM = 0.5

#: 份数合法区间（与 UI QSpinBox 一致）
MIN_COPIES = 1
MAX_COPIES = 999


# ---------------------------------------------------------------------------
# 枚举合并
# ---------------------------------------------------------------------------
def _is_rich(entry: PrinterCapabilities) -> bool:
    """win32 枚举条目通常带驱动名或端口名，视为信息更全的一方。"""
    return bool(entry.driver and entry.driver.driver_name) or \
        bool(entry.port_name)


def _merge_one(entries: list[PrinterCapabilities]) -> PrinterCapabilities:
    """把同名打印机的多条枚举结果合并为一条（字段互补，去重）。"""
    primary = next((e for e in entries if _is_rich(e)), entries[0])
    supplement = next((e for e in entries if e is not primary), None)

    driver = primary.driver
    if (not driver or not driver.driver_name) and supplement is not None:
        driver = supplement.driver

    port_name = primary.port_name or (
        supplement.port_name if supplement else None)
    status = primary.status or (supplement.status if supplement else None)

    dpi = primary.dpi or (supplement.dpi if supplement else None)
    dpi_x = primary.dpi_x or (supplement.dpi_x if supplement else None)
    dpi_y = primary.dpi_y or (supplement.dpi_y if supplement else None)

    # 支持纸张去重（同名同尺寸视为同一项）
    seen: set[tuple[str, float, float]] = set()
    papers: list[PaperSize] = []
    for src in (primary, supplement):
        if src is None:
            continue
        for p in src.supported_papers:
            key = (p.name.lower(), round(p.width_mm, 3), round(p.height_mm, 3))
            if key in seen:
                continue
            seen.add(key)
            papers.append(p)

    transport = primary.transport
    if transport == Transport.UNKNOWN and supplement is not None:
        transport = supplement.transport

    notes_parts = [n for n in (primary.notes,
                               supplement.notes if supplement else None) if n]

    return dataclasses.replace(
        primary,
        driver=driver or DriverInfo(),
        port_name=port_name,
        status=status,
        transport=transport,
        protocol=primary.protocol or Protocol.UNIVERSAL,
        dpi=dpi,
        dpi_x=dpi_x,
        dpi_y=dpi_y,
        supported_papers=tuple(papers),
        max_printable_width_mm=primary.max_printable_width_mm
        or (supplement.max_printable_width_mm if supplement else None),
        max_printable_height_mm=primary.max_printable_height_mm
        or (supplement.max_printable_height_mm if supplement else None),
        supports_custom_paper=primary.supports_custom_paper
        or bool(supplement and supplement.supports_custom_paper),
        supports_true_size=primary.supports_true_size
        or bool(supplement and supplement.supports_true_size),
        is_default=primary.is_default or bool(
            supplement and supplement.is_default),
        verified_with_hardware=primary.verified_with_hardware
        or bool(supplement and supplement.verified_with_hardware),
        notes="；".join(notes_parts),
    )


def merge_capabilities_list(
        enum_result: list[PrinterCapabilities]) -> list[PrinterCapabilities]:
    """合并两路枚举结果：按打印机名分组去重、字段互补。

    同名条目以 win32 侧（带驱动名/端口）为主，Qt 侧补齐缺失字段（dpi 等）；
    默认打印机排在最前，其余按名称排序。空输入返回空列表。
    """
    groups: dict[str, list[PrinterCapabilities]] = {}
    order: list[str] = []
    for item in enum_result or []:
        if item is None or not item.printer_name:
            continue
        key = item.printer_name.strip().lower()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged = [_merge_one(groups[key]) for key in order]
    merged.sort(key=lambda c: (not c.is_default, c.printer_name.lower()))
    return merged


def merge_capabilities(
        enum_result: list[PrinterCapabilities]) -> PrinterCapabilities | None:
    """合并枚举并返回默认打印机（无默认则首台）；都失败/空返回 None。"""
    merged = merge_capabilities_list(enum_result)
    if not merged:
        return None
    return next((c for c in merged if c.is_default), merged[0])


# ---------------------------------------------------------------------------
# 纸张选择
# ---------------------------------------------------------------------------
def pick_paper(capabilities: PrinterCapabilities | None,
               want_w_mm: float, want_h_mm: float) -> PaperSize:
    """在能力表内查找与目标尺寸接近的纸张；未命中回退自定义 PaperSize。

    先按 (w, h) 查找，未命中再按 (h, w)（旋转）查找；仍未命中时构造
    ``自定义 WxH mm`` PaperSize，是否允许自定义由调用方结合
    ``capabilities.supports_custom_paper`` 判定（见 :func:`paper_choice_note`）。
    """
    w = _finite_float(want_w_mm, 0.0)
    h = _finite_float(want_h_mm, 0.0)
    if capabilities is not None:
        matched = capabilities.find_paper(w, h, PAPER_MATCH_TOLERANCE_MM)
        if matched is None:
            matched = capabilities.find_paper(h, w, PAPER_MATCH_TOLERANCE_MM)
        if matched is not None:
            return matched
    return PaperSize(
        name=f"自定义 {w:g}x{h:g}mm",
        width_mm=w,
        height_mm=h,
    )


def paper_choice_note(capabilities: PrinterCapabilities | None,
                      paper: PaperSize) -> str:
    """说明纸张选择结果：能力表命中 / 自定义（是否支持自定义纸张）。"""
    if capabilities is not None and capabilities.find_paper(
            paper.width_mm, paper.height_mm, PAPER_MATCH_TOLERANCE_MM) is not None:
        return "已匹配打印机能力表中的纸张规格"
    if capabilities is not None and capabilities.supports_custom_paper:
        return (f"能力表无完全匹配，使用自定义 {paper.width_mm:g}x"
                f"{paper.height_mm:g}mm（该设备支持自定义纸张）")
    return (f"能力表无完全匹配，使用自定义 {paper.width_mm:g}x"
            f"{paper.height_mm:g}mm；该设备未声明支持自定义纸张，"
            "请确认驱动纸张列表中存在该规格")


# ---------------------------------------------------------------------------
# 纸张一致性决策（纯函数，三态：match / needs_confirm / abort）
# ---------------------------------------------------------------------------
#: 决策状态：模板纸张与所选纸张一致，直接继续
PAPER_MATCH = "match"
#: 决策状态：纸张不同，需用户明确确认是否按所选纸张重新排版
PAPER_NEEDS_CONFIRM = "needs_confirm"
#: 决策状态：用户拒绝转换，中止提交
PAPER_ABORT = "abort"
#: 决策状态：用户确认按所选纸张重新排版（允许转换）
PAPER_CONVERT = "convert"


def _paper_wh(paper) -> tuple[float, float] | None:
    if paper is None:
        return None
    # 兼容 PaperSize（width_mm/height_mm）与模板对象（paper_width/paper_height）
    w = getattr(paper, "width_mm", None)
    h = getattr(paper, "height_mm", None)
    if w is None:
        w = getattr(paper, "paper_width", None)
        h = getattr(paper, "paper_height", None)
    if w is None or h is None:
        return None
    return _finite_float(w, 0.0), _finite_float(h, 0.0)


def _coerce_paper(paper) -> PaperSize | None:
    """把 PaperSize 或模板对象归一为 PaperSize（无法识别返回 None）。"""
    wh = _paper_wh(paper)
    if wh is None:
        return None
    name = getattr(paper, "name", None) or \
        getattr(paper, "template_name", None) or "模板纸张"
    return PaperSize(name=str(name), width_mm=wh[0], height_mm=wh[1])


def paper_sizes_equal(a, b, tolerance_mm: float = PAPER_MATCH_TOLERANCE_MM) -> bool:
    """两张纸张物理尺寸是否一致（容差内，允许宽高互换的方向等价）。"""
    pa, pb = _paper_wh(a), _paper_wh(b)
    if pa is None or pb is None:
        return False
    (aw, ah), (bw, bh) = pa, pb
    same = abs(aw - bw) <= tolerance_mm and abs(ah - bh) <= tolerance_mm
    rotated = abs(aw - bh) <= tolerance_mm and abs(ah - bw) <= tolerance_mm
    return same or rotated


def classify_paper(template_paper, selected_paper) -> str:
    """模板纸张 vs 所选纸张：一致 → match；不同 → needs_confirm。

    不做静默裁切/缩放：不同时必须由 UI 弹出明确提示并获得用户选择。
    """
    if paper_sizes_equal(template_paper, selected_paper):
        return PAPER_MATCH
    return PAPER_NEEDS_CONFIRM


def paper_conversion_prompt(template_paper, selected_paper) -> str:
    """纸张不一致时给用户的确认文案（含两边物理尺寸）。"""
    tw = _paper_wh(template_paper) or (0.0, 0.0)
    sw = _paper_wh(selected_paper) or (0.0, 0.0)
    return (
        f"模板纸张 {tw[0]:g}×{tw[1]:g}mm 与所选纸张 {sw[0]:g}×{sw[1]:g}mm "
        "不一致：将按所选纸张重新排版（标签尺寸不变）。\n\n"
        "· 确认继续：按所选纸张重新排版提交；\n"
        "· 改用模板纸张：放弃所选纸张，回到模板原始纸张；\n"
        "· 取消：中止本次打印。\n"
        "程序不会在未确认的情况下静默裁切或缩放标签。"
    )


def resolve_paper_choice(template_paper, selected_paper, user_choice: str | None):
    """把用户对纸张不一致的选择解析为最终纸张与转换许可。

    ``user_choice``：
    - ``"use_selected"``：确认按所选纸张重新排版 → allow_paper_conversion=True；
    - ``"use_template"``：改用模板纸张；
    - ``"abort"``：中止；
    - ``None``：尚未选择 → needs_confirm（UI 应先弹窗）。

    返回 dict：``state``（match/needs_confirm/convert/abort）、``paper``、
    ``allow_paper_conversion``。
    """
    if classify_paper(template_paper, selected_paper) == PAPER_MATCH:
        return {
            "state": PAPER_MATCH,
            "paper": selected_paper,
            "allow_paper_conversion": False,
        }
    if user_choice == "use_selected":
        return {
            "state": PAPER_CONVERT,
            "paper": selected_paper,
            "allow_paper_conversion": True,
        }
    if user_choice == "use_template":
        return {
            "state": PAPER_MATCH,
            "paper": _coerce_paper(template_paper),
            "allow_paper_conversion": False,
        }
    if user_choice == "abort":
        return {"state": PAPER_ABORT, "paper": None,
                "allow_paper_conversion": False}
    return {"state": PAPER_NEEDS_CONFIRM, "paper": None,
            "allow_paper_conversion": False}


# ---------------------------------------------------------------------------
# 表单 -> 任务规格
# ---------------------------------------------------------------------------
def _finite_float(value, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v or v in (float("inf"), float("-inf")):
        return default
    return v


def _clamped_offset(value, default: float = 0.0) -> float:
    v = _finite_float(value, default)
    return max(-MAX_OFFSET_MM, min(MAX_OFFSET_MM, v))


def _quality(value) -> PrintQuality:
    try:
        return PrintQuality(str(value).lower())
    except (ValueError, TypeError):
        return PrintQuality.NORMAL


def _orientation(value) -> JobOrientation:
    try:
        return JobOrientation(str(value).lower())
    except (ValueError, TypeError):
        return JobOrientation.PORTRAIT


def _copies(value) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return MIN_COPIES
    return max(MIN_COPIES, min(MAX_COPIES, n))


def _dpi(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _template_paper(template_paper) -> PaperSize | None:
    """从 LabelTemplate（或含 paper_width/paper_height 的对象）构造纸张。"""
    if template_paper is None:
        return None
    w = getattr(template_paper, "paper_width", None)
    h = getattr(template_paper, "paper_height", None)
    name = getattr(template_paper, "template_name", None) or "模板纸张"
    if w is None or h is None:
        return None
    paper = PaperSize(name=str(name), width_mm=_finite_float(w, 0.0),
                      height_mm=_finite_float(h, 0.0))
    return paper if paper.is_valid else None


def build_label_content(items, template, form: dict,
                        job: LabelJobSpec | None = None) -> LabelContent | None:
    """从原始编码与模板构建专用协议 :class:`LabelContent`（纯函数）。

    份数语义（冻结）：
    - ``items`` 必须是**未按模板份数展开**的原始编码列表
      ``[(值, 条码类型), ...]``；展开只由协议适配器按 ``template_copies``
      执行一次；
    - 任务份数（``job.copies``）**不**进入本模型，由协议打印数量命令唯一应用；
    - 标签/纸张尺寸、条码高度、show_text、偏移、模块宽、静区模块数、间隙
      全部来自模板/表单/校准配置。

    ``template`` 为 None 或 ``items`` 为空时返回 None（通用队列路径不需要）。
    """
    if not items or template is None:
        return None
    form = form or {}

    label_w = _finite_float(getattr(template, "label_width", None), 40.0)
    label_h = _finite_float(getattr(template, "label_height", None), 25.0)

    paper = form.get("paper")
    if not isinstance(paper, PaperSize):
        paper = _template_paper(template)
    paper_w = _finite_float(
        getattr(paper, "width_mm", None) or label_w, label_w)
    paper_h = _finite_float(
        getattr(paper, "height_mm", None) or label_h, label_h)

    module_mm = _finite_float(form.get("module_mm"), GS1_MODULE_MM)
    if module_mm <= 0:
        module_mm = GS1_MODULE_MM

    offset_x = _clamped_offset(
        form.get("offset_x_mm")
        if job is None else job.offset_x_mm)
    offset_y = _clamped_offset(
        form.get("offset_y_mm")
        if job is None else job.offset_y_mm)

    gap = _finite_float(form.get("label_gap_mm"), 0.0)
    if gap < 0:
        gap = 0.0

    copies = getattr(template, "copies", 1)
    try:
        template_copies = max(1, int(copies))
    except (TypeError, ValueError):
        template_copies = 1

    return LabelContent(
        items=[(str(v), str(t)) for v, t in items],
        template_copies=template_copies,
        label_width_mm=label_w,
        label_height_mm=label_h,
        paper_width_mm=paper_w,
        paper_height_mm=paper_h,
        barcode_height_mm=_finite_float(
            getattr(template, "barcode_height", None), 15.0),
        show_text=bool(getattr(template, "show_text", True)),
        offset_x_mm=offset_x,
        offset_y_mm=offset_y,
        module_mm=module_mm,
        quiet_modules=DEFAULT_QUIET_MODULES,
        label_gap_mm=gap,
    )


def build_job_from_form(template_paper, capabilities,
                        form: dict, *, items=None, template=None) -> LabelJobSpec:
    """从表单字典构造 :class:`LabelJobSpec`，非法值静默回退默认。

    form 支持的键：
    - ``paper``            : PaperSize（优先）；缺省回退模板纸张；
    - ``offset_x_mm`` / ``offset_y_mm``：校准偏移（mm，钳制在 ±500mm）；
    - ``copies``           : 任务份数，钳制 1..999（唯一应用点，不进 PDF/内容）；
    - ``quality``          : draft/normal/high，非法回退 normal；
    - ``orientation``     : portrait/landscape，非法回退 portrait；
    - ``dpi``              : 正整数，非法回退能力表原生分辨率/None；
    - ``label_gap_mm``     : 标签间隙（mm，非负）；
    - ``quiet_zone_mm``   : 静区物理宽度（mm，非负）；缺省 = 11 模块 × module_mm；
    - ``module_mm``        : 条码模块宽度，缺省 0.33mm（GS1 标称）；
    - ``scale_tolerance_mm``：缩放检测容差，缺省 0.5mm；
    - ``allow_paper_conversion``：用户已明确确认的纸张转换许可（bool）。

    可选关键字：
    - ``items``    : 未展开原始编码列表（专用协议路径构建 label_content）；
    - ``template`` : 完整模板对象（提供 label 尺寸/barcode_height/show_text/
      copies）；与 items 同时给出时填充 ``job.label_content``，内容模型不含
      任务份数。
    """
    form = form or {}

    paper = form.get("paper")
    if not isinstance(paper, PaperSize):
        paper = _template_paper(template_paper)

    module_mm = _finite_float(form.get("module_mm"), GS1_MODULE_MM)
    if module_mm <= 0:
        module_mm = GS1_MODULE_MM

    quiet_zone_mm = form.get("quiet_zone_mm")
    if quiet_zone_mm is None:
        quiet_zone_mm = DEFAULT_QUIET_MODULES * module_mm
    else:
        quiet_zone_mm = _finite_float(quiet_zone_mm, DEFAULT_QUIET_MODULES * module_mm)
        if quiet_zone_mm < 0:
            quiet_zone_mm = DEFAULT_QUIET_MODULES * module_mm

    dpi = _dpi(form.get("dpi"))
    if dpi is None and capabilities is not None:
        dpi = capabilities.resolution()

    gap = _finite_float(form.get("label_gap_mm"), 0.0)
    if gap < 0:
        gap = 0.0

    job = LabelJobSpec(
        paper=paper,
        orientation=_orientation(form.get("orientation")),
        copies=_copies(form.get("copies")),
        quality=_quality(form.get("quality")),
        dpi=dpi,
        offset_x_mm=_clamped_offset(form.get("offset_x_mm")),
        offset_y_mm=_clamped_offset(form.get("offset_y_mm")),
        label_gap_mm=gap,
        quiet_zone_mm=quiet_zone_mm,
        module_mm=module_mm,
        scale_tolerance_mm=_finite_float(
            form.get("scale_tolerance_mm"), 0.5),
        allow_paper_conversion=bool(form.get("allow_paper_conversion", False)),
    )

    # 专用协议内容模型：items 未展开，任务份数 job.copies 不进入内容
    content = build_label_content(items, template, form, job)
    if content is not None:
        job.label_content = content
    return job


# ---------------------------------------------------------------------------
# 摘要与分组
# ---------------------------------------------------------------------------
def summarize_capabilities(caps: PrinterCapabilities | None) -> str:
    """一行摘要：驱动名 / 端口 / 默认 / 状态（未实测另由调用方加注）。"""
    if caps is None:
        return "未检测到打印机（需 Windows + 打印服务）"
    parts: list[str] = []
    driver_name = caps.driver.driver_name if caps.driver else None
    parts.append(f"驱动：{driver_name or '未知'}")
    parts.append(f"端口：{caps.port_name or '未知'}")
    if caps.is_default:
        parts.append("默认打印机")
    status_text = caps.status.describe() if caps.status else "未知状态"
    parts.append(f"状态：{status_text}")
    return "；".join(parts)


def group_issues(issues: list[ValidationIssue]) -> tuple[list, list]:
    """把校验问题分为 (errors, warnings)，保持原顺序。"""
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity != "error"]
    return errors, warnings


# ---------------------------------------------------------------------------
# 打印方式（协议/通道）：只按用户显式选择，禁止品牌推断
# ---------------------------------------------------------------------------
def transport_honesty(caps: PrinterCapabilities | None) -> tuple[bool, str]:
    """如实描述连接通道是否可用。

    返回 ``(enabled, 文案)``：
    - network：TCP 9100 raw 可用（无设备确认回执）；
    - local：本机系统队列（通用路径）；
    - usb / bluetooth / lpr：未集成（不支持），禁用；
    - unknown：未知连接方式，提示需经 Windows 厂商队列。
    """
    if caps is None:
        return False, "未检测到打印机"
    transport = caps.transport
    port = (caps.port_name or "").strip().lower()
    if port.startswith(("lpr://", "lpr:")):
        return False, "LPR（RFC 1179）通道未集成（不支持），请改用 9100 TCP 或 Windows 厂商队列"
    if transport == Transport.NETWORK:
        return True, f"网络通道：TCP 9100 raw（地址 {caps.port_name or '未配置'}；发送成功无设备确认回执，不代表物理打印完成）"
    if transport == Transport.USB:
        return False, "USB 直连通道未集成（不支持），请把设备接入 Windows 厂商驱动队列"
    if transport == Transport.BLUETOOTH:
        return False, "蓝牙直连通道未集成（不支持），请改用网络 9100 或 Windows 厂商队列"
    if transport == Transport.LOCAL:
        return True, "本机系统队列（通用 Windows 打印路径）"
    return False, "未知连接方式：请经 Windows 厂商驱动队列打印，或配置网络 9100 地址"


def mode_basis_text(caps: PrinterCapabilities | None, mode: str,
                    sub_protocol: str | None = None) -> str:
    """打印方式选择依据文案（只来自显式选择/能力声明，不做品牌推断）。"""
    if caps is None:
        return ""
    if mode == MODE_UNIVERSAL:
        return "通用 Windows 队列：由用户显式选择，经系统驱动与队列打印。"
    # 专用协议 / TCP 直连：按用户显式选择的协议构造临时声明再取依据文案，
    # 不读取厂商名/驱动名做品牌推断
    import dataclasses as _dc
    proto_name = sub_protocol if mode == MODE_TCP9100 else mode
    try:
        declared = _dc.replace(caps, protocol=Protocol(proto_name))
    except (ValueError, TypeError):
        declared = caps
    base = protocol_selection_basis(declared)
    if mode == MODE_TCP9100:
        base = "网络 TCP 9100 raw 直连（无设备确认回执）；" + base
    return base


def effective_capabilities(caps: PrinterCapabilities, mode: str,
                           *, sub_protocol: str | None = None,
                           host_port: str | None = None
                           ) -> PrinterCapabilities:
    """按用户显式选择的打印方式派生提交用能力模型（不改动原始枚举）。

    - universal：原样返回（protocol=UNIVERSAL，本机队列）；
    - zpl/tspl/cpcl：把 ``protocol`` 替换为用户显式选择的协议；
    - tcp9100：transport=NETWORK、port_name=host_port，protocol 取
      ``sub_protocol``（zpl/tspl/cpcl），禁止按品牌推断。
    """
    import dataclasses as _dc

    if caps is None:
        raise PrintError(
            PrintError.INVALID_JOB, "缺少打印机能力信息，无法选择打印方式")

    if mode == MODE_TCP9100:
        proto_name = (sub_protocol or mode).lower()
        try:
            proto = Protocol(proto_name)
        except (ValueError, TypeError):
            raise PrintError(
                PrintError.PROTOCOL_UNSUPPORTED,
                f"TCP 9100 直连需配合专用协议（zpl/tspl/cpcl），当前 {sub_protocol!r}",
            )
        endpoint = (host_port or "").strip()
        if not endpoint:
            raise PrintError(
                PrintError.INVALID_JOB,
                "网络 TCP 9100 需要填写打印机地址（IP:端口，如 192.168.1.20:9100）",
            )
        return _dc.replace(caps, protocol=proto, transport=Transport.NETWORK,
                           port_name=endpoint)

    if mode in PROTOCOL_MODES:
        return _dc.replace(caps, protocol=Protocol(mode))

    # universal：保持原能力（含原 transport/protocol 声明）
    return caps


def capability_hints(caps: PrinterCapabilities | None) -> list[str]:
    """设备未声明能力的如实提示（不夸大、不伪造实测）。"""
    hints: list[str] = []
    if caps is None:
        return hints
    if not caps.supports_gap_sensor:
        hints.append(
            "该设备不支持间隙传感，标签间隙仅用于专用协议打印/在本机不生效")
    if not caps.supports_true_size:
        hints.append("设备未声明支持 1:1 打印，尺寸可能被驱动缩放，请先以测试页复核")
    if not caps.verified_with_hardware:
        hints.append("未经真实设备实测，请先以测试页复核输出")
    return hints
