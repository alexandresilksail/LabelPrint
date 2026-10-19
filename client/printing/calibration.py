"""标签打印校准工具集（纯计算，不依赖 Qt / pywin32）。

本模块全部逻辑只做几何与规格校验，可在任意平台（含 Linux 测试环境）
直接导入运行；与真实设备交互（提交、查询队列）一律由适配器层负责。

能力约定：
- 所有长度单位统一为毫米（mm）；ReportLab 内部换算点（pt）。
- 静区模块数要求与 :mod:`client.printing.pdf` 的 ``DEFAULT_QUIET_MODULES=11``
  保持一致，并符合 GS1 通用规范对条码每侧静区模块数的要求。
- 模块宽度允许区间取自 :mod:`client.printing.templates` 的 GS1 常量
  （0.264mm ~ 0.66mm，标称 0.33mm 的 80%~200%）。
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Tuple

from .capabilities import PaperSize, PrinterCapabilities
from .errors import PrintError
from .job import LabelJobSpec, ScaleReport, ValidationIssue
from .templates import GS1_MAX_MODULE_MM, GS1_MIN_MODULE_MM

#: 每侧默认静区模块数（与 pdf.py DEFAULT_QUIET_MODULES=11、GS1 规范一致）
DEFAULT_QUIET_MODULES = 11


# ---------------------------------------------------------------------------
# 偏移（纯函数）
# ---------------------------------------------------------------------------
def compute_shifted_position(x_mm: float, y_mm: float,
                             offset_x: float, offset_y: float
                             ) -> Tuple[float, float]:
    """把版面坐标加上校准偏移，返回新坐标 (x_mm, y_mm)。纯函数。"""
    return x_mm + offset_x, y_mm + offset_y


def apply_offset(job: LabelJobSpec, offset_x_mm: float,
                 offset_y_mm: float) -> LabelJobSpec:
    """返回设置偏移后的任务副本，原任务不被修改。纯函数。"""
    return dataclasses.replace(
        job, offset_x_mm=float(offset_x_mm), offset_y_mm=float(offset_y_mm),
    )


# ---------------------------------------------------------------------------
# 条码规格校验
# ---------------------------------------------------------------------------
def check_quiet_zone(module_mm: float, quiet_modules: int,
                     required_modules: int = DEFAULT_QUIET_MODULES
                     ) -> ValidationIssue | None:
    """校验条码每侧静区宽度是否满足 GS1 通用规范的模块数要求。

    静区物理宽度 = ``quiet_modules * module_mm``，要求宽度 =
    ``required_modules * module_mm``（默认 11 模块，与现有 pdf.py
    ``DEFAULT_QUIET_MODULES=11`` 一致；GS1 通用规范要求 EAN/UPC 等
    条码每侧保留足够模块数的空白静区以保证稳定识读）。

    模块数不足（临界相等视为通过）返回 ``QUIET_ZONE_TOO_SMALL`` 错误；
    满足则返回 None。
    """
    required_w = required_modules * module_mm
    actual_w = quiet_modules * module_mm
    if actual_w < required_w - 1e-9:
        return ValidationIssue.error(
            PrintError.QUIET_ZONE_TOO_SMALL,
            f"静区仅 {quiet_modules} 模块（{actual_w:.3f}mm），"
            f"少于要求的 {required_modules} 模块（{required_w:.3f}mm），"
            "可能无法稳定识读",
        )
    return None


def check_module_width(module_mm: float) -> ValidationIssue | None:
    """校验模块宽度是否在 GS1 允许区间 [0.264mm, 0.66mm]（含端点）。

    超出区间返回 ``MODULE_WIDTH_OUT_OF_RANGE`` 错误，否则 None。
    """
    if module_mm < GS1_MIN_MODULE_MM - 1e-9 or module_mm > GS1_MAX_MODULE_MM + 1e-9:
        return ValidationIssue.error(
            PrintError.MODULE_WIDTH_OUT_OF_RANGE,
            f"模块宽度 {module_mm:.3f}mm 超出可识读区间 "
            f"[{GS1_MIN_MODULE_MM}, {GS1_MAX_MODULE_MM}]mm",
        )
    return None


# ---------------------------------------------------------------------------
# 纸张 / 设备几何校验
# ---------------------------------------------------------------------------
def check_label_geometry(paper: PaperSize,
                         capabilities: PrinterCapabilities,
                         label_gap_mm: float = 0.0) -> list[ValidationIssue]:
    """校验纸张与打印机能力是否匹配。

    - 纸张非法（非正尺寸）→ ``PAPER_UNSUPPORTED`` error；
    - ``can_print_size`` 失败：支持列表中能找到近似规格（或设备支持自定义
      纸张）但尺寸超出可打印区域 → ``PAPER_OUT_OF_RANGE`` error；
      否则 → ``PAPER_UNSUPPORTED`` error；
    - ``label_gap_mm > 0`` 但能力模型未声明间隙传感支持
      （可选属性 ``supports_gap_sensor`` 缺省视为不支持）→ warning，
      说明设备可能无法自动定位标签间隙；
    - 能力模型未声明支持真实尺寸（1:1）打印 → warning，提示 1:1 不保证。
    """
    issues: list[ValidationIssue] = []

    if not paper.is_valid:
        issues.append(ValidationIssue.error(
            PrintError.PAPER_UNSUPPORTED,
            f"纸张规格非法：{paper.width_mm}x{paper.height_mm}mm",
        ))
        return issues

    if not capabilities.can_print_size(paper):
        matched = capabilities.find_paper(paper.width_mm, paper.height_mm)
        if matched is not None or capabilities.supports_custom_paper:
            code = PrintError.PAPER_OUT_OF_RANGE
            msg = (f"{paper.width_mm:.1f}x{paper.height_mm:.1f}mm 超出 "
                   f"{capabilities.printer_name} 的可打印区域")
        else:
            code = PrintError.PAPER_UNSUPPORTED
            msg = (f"{capabilities.printer_name} 不支持 "
                   f"{paper.width_mm:.1f}x{paper.height_mm:.1f}mm 纸张规格")
        issues.append(ValidationIssue.error(code, msg))

    if label_gap_mm > 0 and not getattr(capabilities, "supports_gap_sensor", False):
        issues.append(ValidationIssue.warning(
            "GAP_SENSOR_UNSUPPORTED",
            f"设置了 {label_gap_mm:.1f}mm 标签间隙，但该设备能力模型未声明"
            "间隙传感支持，可能无法自动定位标签间隙，请确认设备配置",
        ))

    if not capabilities.supports_true_size:
        issues.append(ValidationIssue.warning(
            "TRUE_SIZE_UNSUPPORTED",
            "该设备未声明支持真实尺寸（1:1）打印，实际输出尺寸可能被驱动"
            "缩放，建议打印后用测试页复核",
        ))

    return issues


# ---------------------------------------------------------------------------
# 测试页
# ---------------------------------------------------------------------------
def render_test_page_pdf(path, paper: PaperSize,
                         dpi_hint: int | None = None) -> dict:
    """生成标准校准测试页 PDF（页面物理尺寸精确等于 paper）。

    页面内容：
    - 四边毫米标尺（1mm 小刻度；5/10mm 长刻度并标注数字）；
    - 5mm 浅色网格；
    - 四角 + 中心十字注册标记；
    - 100mm×100mm 基准方块（页面放不下时自动省略）；
    - 灰阶条（打印质量目测用）；
    - 页内文字标注纸张规格与生成时间。

    返回 meta ``{path, pages: 1, width_mm, height_mm}``。
    ``dpi_hint`` 仅作记录，不改变物理页面尺寸。
    """
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas

    w_mm, h_mm = float(paper.width_mm), float(paper.height_mm)
    c = rl_canvas.Canvas(str(path), pagesize=(w_mm * mm, h_mm * mm))
    c.setTitle(f"校准测试页 {paper.name} {w_mm:g}x{h_mm:g}mm")

    edge = 4.0  # 标尺留白 mm

    # ---- 5mm 浅色网格 ----
    c.setStrokeColorRGB(0.88, 0.88, 0.88)
    c.setLineWidth(0.15)
    x = edge
    while x <= w_mm - edge:
        c.line(x * mm, edge * mm, x * mm, (h_mm - edge) * mm)
        x += 5.0
    y = edge
    while y <= h_mm - edge:
        c.line(edge * mm, y * mm, (w_mm - edge) * mm, y * mm)
        y += 5.0

    # ---- 四边毫米标尺 ----
    c.setStrokeColorRGB(0, 0, 0)
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 2.2 * mm * 0.75)

    def h_ruler(x0: float, x1: float, baseline_y: float, upward: bool):
        """水平标尺：x0..x1（mm），基线 y；upward 决定刻度朝页面内。"""
        n = int(round(x1 - x0))
        for i in range(n + 1):
            xx = x0 + i
            if i % 10 == 0:
                tick, label = 3.0, True
            elif i % 5 == 0:
                tick, label = 2.0, True
            else:
                tick, label = 1.0, False
            y2 = baseline_y + (tick if upward else -tick)
            c.setLineWidth(0.3 if i % 5 == 0 else 0.15)
            c.line(xx * mm, baseline_y * mm, xx * mm, y2 * mm)
            if label:
                ty = y2 + (1.0 if upward else -3.2)
                c.drawCentredString(xx * mm, ty * mm, str(i))

    def v_ruler(y0: float, y1: float, baseline_x: float, right: bool):
        n = int(round(y1 - y0))
        for i in range(n + 1):
            yy = y0 + i
            if i % 10 == 0:
                tick, label = 3.0, True
            elif i % 5 == 0:
                tick, label = 2.0, True
            else:
                tick, label = 1.0, False
            x2 = baseline_x + (tick if right else -tick)
            c.setLineWidth(0.3 if i % 5 == 0 else 0.15)
            c.line(baseline_x * mm, yy * mm, x2 * mm, yy * mm)
            if label:
                c.saveState()
                c.translate(x2 + (1.0 if right else -3.2) * mm, yy * mm)
                c.rotate(90 if right else -90)
                c.drawCentredString(0, 0, str(i))
                c.restoreState()

    h_ruler(edge, w_mm - edge, h_mm - edge, False)   # 顶边，刻度朝下
    h_ruler(edge, w_mm - edge, edge, True)           # 底边，刻度朝上
    v_ruler(edge, h_mm - edge, edge, True)          # 左边，刻度朝右
    v_ruler(edge, h_mm - edge, w_mm - edge, False)   # 右边，刻度朝左

    # ---- 四角 + 中心十字注册标记 ----
    def cross(cx: float, cy: float, size: float = 4.0):
        c.setLineWidth(0.4)
        c.line((cx - size) * mm, cy * mm, (cx + size) * mm, cy * mm)
        c.line(cx * mm, (cy - size) * mm, cx * mm, (cy + size) * mm)

    for cx, cy in ((edge, edge), (w_mm - edge, edge),
                   (edge, h_mm - edge), (w_mm - edge, h_mm - edge)):
        cross(cx, cy)
    cross(w_mm / 2, h_mm / 2, size=6.0)

    # ---- 100x100mm 基准方块（页面足够大时绘制）----
    if w_mm >= 100 and h_mm >= 100:
        bx = (w_mm - 100) / 2
        by = (h_mm - 100) / 2
        c.setLineWidth(0.6)
        c.rect(bx * mm, by * mm, 100 * mm, 100 * mm, fill=0, stroke=1)
        c.setFont("Helvetica", 2.6 * mm * 0.75)
        c.drawCentredString((bx + 50) * mm, (by + 102) * mm,
                            "100 mm x 100 mm 基准方块")

    # ---- 灰阶条（10 级）----
    bar_w = min(80.0, w_mm - 2 * edge)
    bar_x = (w_mm - bar_w) / 2
    bar_y = edge + 6.0
    sw = bar_w / 10
    for i in range(10):
        g = i / 9.0
        c.setFillColorRGB(g, g, g)
        c.rect((bar_x + i * sw) * mm, bar_y * mm, sw * mm, 6 * mm,
               fill=1, stroke=0)
    c.setFillColorRGB(0, 0, 0)

    # ---- 文字标注 ----
    c.setFont("Helvetica-Bold", 3.2 * mm * 0.75)
    c.drawString(edge * mm, (h_mm - edge - 6) * mm,
                 f"校准测试页  {paper.name}  {w_mm:g} x {h_mm:g} mm")
    c.setFont("Helvetica", 2.4 * mm * 0.75)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    hint = f"  dpi_hint={dpi_hint}" if dpi_hint else ""
    c.drawString(edge * mm, (h_mm - edge - 10) * mm,
                 f"生成时间 {now}{hint}；请用刻度尺复核 mm 标尺与基准方块")

    c.showPage()
    c.save()

    return {
        "path": str(path),
        "pages": 1,
        "width_mm": w_mm,
        "height_mm": h_mm,
    }


# ---------------------------------------------------------------------------
# 缩放检测（惰性对接 pdf_render，A 切片并行实现中）
# ---------------------------------------------------------------------------
def verify_scale(pdf_path: str, paper: PaperSize,
                 tolerance_mm: float = 0.5) -> ScaleReport:
    """检测已生成 PDF 的物理尺寸是否与目标纸张一致（1:1）。

    惰性导入 ``client.printing.pdf_render.detect_scale_mismatch``（公共接口
    已冻结：``detect_scale_mismatch(path, paper, tolerance_mm) -> ScaleReport``，
    由并行 A 切片实现）。模块尚不可导入（或接口缺失）时抛出明确的
    ``PrintError(UNSUPPORTED_PLATFORM)``，不在此处伪造检测结果。

    集成阶段 pdf_render 就绪后全量重跑本用例。
    """
    try:
        from .pdf_render import detect_scale_mismatch
    except (ImportError, AttributeError) as exc:
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            "pdf_render 缩放检测模块尚未就绪，无法执行 verify_scale",
            resolution="集成阶段 pdf_render.detect_scale_mismatch 提供后重跑",
            cause=exc,
        ) from exc
    return detect_scale_mismatch(pdf_path, paper, tolerance_mm)
