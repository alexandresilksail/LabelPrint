"""单位换算唯一来源（mm ↔ PDF point ↔ 设备 dots ↔ DPI）。

全项目尺寸换算必须收敛到本模块，禁止各处自行乘除：
- PDF point：1pt = 1/72 inch（ReportLab / Qt QPdfDocument 页面单位）
- 设备 dot ：1dot = 1/dpi inch（ZPL/TSPL/CPCL 指令坐标单位）
- 毫米 mm   ：业务与 UI 层唯一单位

``orient_paper`` 统一横向/纵向处理：方向语义在进入渲染/后端前
先归一为物理尺寸（横向 = 宽 ≥ 高），避免 Qt/驱动重复换向。
"""

from __future__ import annotations

from .capabilities import JobOrientation, PaperSize

MM_PER_INCH = 25.4
PT_PER_INCH = 72.0
DEFAULT_TOLERANCE_MM = 0.5


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
