"""单位换算唯一来源（mm ↔ PDF point ↔ 设备 dots ↔ DPI）。

全项目尺寸换算必须收敛到本模块，禁止各处自行乘除：
- PDF point：1pt = 1/72 inch（ReportLab / Qt QPdfDocument 页面单位）
- 设备 dot ：1dot = 1/dpi inch（ZPL/TSPL/CPCL 指令坐标单位）
- 毫米 mm   ：业务与 UI 层唯一单位

``orient_paper`` 统一横向/纵向处理：方向语义在进入渲染/后端前
先归一为物理尺寸（横向 = 宽 ≥ 高），避免 Qt/驱动重复换向。

# 静区（quiet zone）与标签间隙（label gap）语义严禁混用（冻结）：
- **静区 quiet zone**：条码左右（及上下）两侧的空白**识读保护带**，属于条码
  图形本身的一部分，UI/配置层以**毫米**保存（``quiet_zone_mm``），渲染/编码层
  以**模块数**（quiet modules）工作；两者唯一换算入口为本模块
  :func:`quiet_zone_mm_to_modules` / :func:`quiet_modules_to_mm`。
- **标签间隙 label_gap_mm**：标签纸相邻两张标签之间的**物理介质间隙**
  （走纸/定位用，仅专用协议 TSPL GAP、ZPL 传感等与设备硬件参数相关），
  **严禁**把它加进条码静区或条码内容布局；PDF 渲染器与
  :mod:`client.printing.preview` 的 ``fit_barcode`` 都不接受该参数。
"""

from __future__ import annotations

from .capabilities import JobOrientation, PaperSize

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0
DEFAULT_TOLERANCE_MM = 0.5

#: GS1 默认每侧静区模块数（与 pdf.DEFAULT_QUIET_MODULES /
#: calibration.DEFAULT_QUIET_MODULES 一致）
GS1_DEFAULT_QUIET_MODULES = 11


def mm_to_pt(mm: float) -> float:
    return mm * PT_PER_INCH / MM_PER_INCH


def pt_to_mm(pt: float) -> float:
    return pt * MM_PER_INCH / PT_PER_INCH


def mm_to_dots(mm: float, dpi: int) -> int:
    """毫米 → 设备点（四舍五入）。dpi 必须为正整数。"""
    if not dpi or dpi <= 0:
        raise ValueError(f"非法 DPI: {dpi!r}")
    return int(round(mm / MM_PER_INCH * dpi))


def dots_to_mm(dots: int, dpi: int) -> float:
    if not dpi or dpi <= 0:
        raise ValueError(f"非法 DPI: {dpi!r}")
    return dots * MM_PER_INCH / dpi


def size_close(a_mm: float, b_mm: float,
               tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> bool:
    """两个毫米尺寸是否在容差内一致。"""
    return abs(a_mm - b_mm) <= tolerance_mm


def paper_matches(a: PaperSize | None, b: PaperSize | None,
                  tolerance_mm: float = DEFAULT_TOLERANCE_MM) -> bool:
    """两张纸规格是否一致（含容差，不考虑方向）。"""
    if a is None or b is None:
        return a is b
    return size_close(a.width_mm, b.width_mm, tolerance_mm) and \
        size_close(a.height_mm, b.height_mm, tolerance_mm)


def orient_paper(paper: PaperSize,
                 orientation: JobOrientation) -> PaperSize:
    """按方向归一物理尺寸：横向 = 宽 ≥ 高，纵向 = 高 ≥ 宽。

    返回新的 PaperSize（不修改入参）；方向语义在渲染/后端之前
    统一到此，后续一律以宽高物理值判定，不再二次换向。
    """
    w, h = paper.width_mm, paper.height_mm
    if orientation == JobOrientation.LANDSCAPE:
        if w < h:
            return PaperSize(paper.name, h, w)
    else:  # PORTRAIT
        if w > h:
            return PaperSize(paper.name, h, w)
    return paper


# ---------------------------------------------------------------------------
# 静区：毫米（UI/配置层）↔ 模块数（渲染/编码层）唯一换算入口
#
# 注意：这里只换算"条码静区"。标签物理间隙 ``label_gap_mm`` 是走纸/定位的
# 硬件介质参数，不属于条码图形，禁止经由本函数换算或叠加进静区。
# ---------------------------------------------------------------------------
def quiet_zone_mm_to_modules(quiet_zone_mm: float, module_mm: float) -> int:
    """毫米静区宽度 → 静区模块数（每侧）。唯一换算入口。

    公式：``round(quiet_zone_mm / module_mm)``，结果 ≥ 0。

    - ``quiet_zone_mm``：用户配置的每侧静区物理宽度（mm，非负）；
    - ``module_mm``：当前条码模块宽度（mm，**必须为正数**）。

    非法输入显式抛 ``ValueError``（绝不静默回退成错误静区）：
    ``module_mm`` 非正、``quiet_zone_mm`` 为 None 或负数。
    """
    if module_mm is None or module_mm <= 0:
        raise ValueError(
            f"非法模块宽度: {module_mm!r}（静区换算要求正数 mm）")
    if quiet_zone_mm is None or quiet_zone_mm < 0:
        raise ValueError(
            f"非法静区宽度: {quiet_zone_mm!r}（静区必须为非负 mm）")
    return max(0, int(round(quiet_zone_mm / module_mm)))


def quiet_modules_to_mm(modules: int, module_mm: float) -> float:
    """静区模块数 → 毫米（与 :func:`quiet_zone_mm_to_modules` 互逆）。

    ``module_mm`` 非正 → ``ValueError``；``modules`` 为负 → ``ValueError``。
    """
    if module_mm is None or module_mm <= 0:
        raise ValueError(
            f"非法模块宽度: {module_mm!r}（静区换算要求正数 mm）")
    if modules < 0:
        raise ValueError(f"静区模块数不能为负: {modules!r}")
    return modules * module_mm
