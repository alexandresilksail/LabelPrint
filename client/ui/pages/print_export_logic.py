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
from ...printing.config_store import MAX_OFFSET_MM
from ...printing.job import LabelJobSpec, ValidationIssue
from ...printing.templates import GS1_MODULE_MM

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


def build_job_from_form(template_paper, capabilities,
                        form: dict) -> LabelJobSpec:
    """从表单字典构造 :class:`LabelJobSpec`，非法值静默回退默认。

    form 支持的键：
    - ``paper``            : PaperSize（优先）；缺省回退模板纸张；
    - ``offset_x_mm`` / ``offset_y_mm``：校准偏移（mm，钳制在 ±500mm）；
    - ``copies``           : 份数，钳制 1..999；
    - ``quality``          : draft/normal/high，非法回退 normal；
    - ``orientation``     : portrait/landscape，非法回退 portrait；
    - ``dpi``              : 正整数，非法回退能力表原生分辨率/None；
    - ``label_gap_mm``     : 标签间隙（mm，非负）；
    - ``quiet_zone_mm``   : 静区物理宽度（mm，非负）；缺省 = 11 模块 × module_mm；
    - ``module_mm``        : 条码模块宽度，缺省 0.33mm（GS1 标称）；
    - ``scale_tolerance_mm``：缩放检测容差，缺省 0.5mm。
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

    return LabelJobSpec(
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
    )


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
