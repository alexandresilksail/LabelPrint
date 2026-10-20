"""qt_print 语义冻结测试：有效页面解析三态、单位换算边界、平台守卫。

全部为纯函数 / 平台分支断言，不触碰真实 Qt 后端与硬件；
Linux 上 ``qt_print_available()==False``，``print_pdf_via_qt`` 直接抛
``PrintError(UNSUPPORTED_PLATFORM)``，禁止 mock 假装调用成功。

运行方式（仓库根目录）：
    python3 -m pytest tests/test_qt_print_semantics.py -q
"""

import pytest

from client.printing import qt_print, units
from client.printing.capabilities import (
    JobOrientation, PaperSize, PrintQuality,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec


# ---------------------------------------------------------------------------
# resolve_effective_page：三态 + 容差边界 + 旋转互换
# ---------------------------------------------------------------------------

def _spec(paper=None, allow=False, tol=0.5):
    return LabelJobSpec(
        paper=paper,
        allow_paper_conversion=allow,
        scale_tolerance_mm=tol,
        copies=1,
    )


def test_resolve_matches_within_tolerance_returns_spec_paper():
    paper = PaperSize("50x30", 50.0, 30.0)
    effective, note, error = qt_print.resolve_effective_page(
        50.0, 30.0, _spec(paper))
    assert error is None
    assert note is None
    assert effective == paper


def test_resolve_swapped_orientation_still_matches():
    paper = PaperSize("50x30", 50.0, 30.0)
    # PDF 以 30×50（旋转后）落盘，容差内互换视为匹配
    effective, note, error = qt_print.resolve_effective_page(
        30.0, 50.0, _spec(paper))
    assert error is None
    assert effective == paper


def test_resolve_mismatch_without_conversion_permission_errors():
    paper = PaperSize("50x30", 50.0, 30.0)
    effective, note, error = qt_print.resolve_effective_page(
        100.0, 100.0, _spec(paper, allow=False))
    assert effective is None
    assert note is None
    assert isinstance(error, PrintError)
    assert error.code == PrintError.PAPER_SIZE_MISMATCH


def test_resolve_mismatch_with_conversion_uses_measured_page():
    paper = PaperSize("50x30", 50.0, 30.0)
    effective, note, error = qt_print.resolve_effective_page(
        100.0, 100.0, _spec(paper, allow=True))
    assert error is None
    assert effective is not None
    assert effective.width_mm == 100.0
    assert effective.height_mm == 100.0
    assert note is not None
    assert "已按用户确认转换" in note
    assert "1:1" in note


def test_resolve_tolerance_boundary_exact_deviation_matches():
    paper = PaperSize("50x30", 50.0, 30.0)
    # 偏差恰好等于容差（<=）→ 匹配，不报错
    effective, note, error = qt_print.resolve_effective_page(
        50.5, 30.0, _spec(paper, tol=0.5))
    assert error is None
    assert effective == paper


def test_resolve_tolerance_just_above_boundary_mismatches():
    paper = PaperSize("50x30", 50.0, 30.0)
    effective, note, error = qt_print.resolve_effective_page(
        50.6, 30.0, _spec(paper, allow=False, tol=0.5))
    assert effective is None
    assert isinstance(error, PrintError)
    assert error.code == PrintError.PAPER_SIZE_MISMATCH


def test_resolve_missing_paper_returns_invalid_job():
    effective, note, error = qt_print.resolve_effective_page(
        50.0, 30.0, _spec(None))
    assert effective is None
    assert isinstance(error, PrintError)
    assert error.code == PrintError.INVALID_JOB


# ---------------------------------------------------------------------------
# units.mm_to_dots / orient_paper 边界（换算唯一来源）
# ---------------------------------------------------------------------------

def test_mm_to_dots_basic_conversion():
    # 25.4mm = 1 inch → 300dpi 即 300 dots
    assert units.mm_to_dots(25.4, 300) == 300


def test_mm_to_dots_rounds_to_nearest_dot():
    # 1mm @203dpi ≈ 7.992 dots → 四舍五入 8
    assert units.mm_to_dots(1.0, 203) == 8


def test_mm_to_dots_rejects_invalid_dpi():
    with pytest.raises(ValueError):
        units.mm_to_dots(10.0, 0)
    with pytest.raises(ValueError):
        units.mm_to_dots(10.0, -1)


def test_orient_paper_landscape_swaps_portrait_dimensions():
    paper = PaperSize("label", 30.0, 50.0)  # 高 > 宽的纵向规格
    out = units.orient_paper(paper, JobOrientation.LANDSCAPE)
    assert out.width_mm == 50.0
    assert out.height_mm == 30.0
    # 不修改入参
    assert paper.width_mm == 30.0 and paper.height_mm == 50.0


def test_orient_paper_already_landscape_kept():
    paper = PaperSize("label", 50.0, 30.0)  # 宽 ≥ 高，已是横向
    out = units.orient_paper(paper, JobOrientation.LANDSCAPE)
    assert out.width_mm == 50.0
    assert out.height_mm == 30.0


def test_orient_paper_portrait_swaps_landscape_back():
    paper = PaperSize("label", 50.0, 30.0)
    out = units.orient_paper(paper, JobOrientation.PORTRAIT)
    assert out.width_mm == 30.0
    assert out.height_mm == 50.0


# ---------------------------------------------------------------------------
# 平台守卫：Linux 上后端不可用，直接抛 UNSUPPORTED_PLATFORM
# ---------------------------------------------------------------------------

def test_qt_print_available_false_on_linux():
    assert qt_print.qt_print_available() is False


def test_print_pdf_via_qt_raises_unsupported_platform_on_linux():
    spec = LabelJobSpec(pdf_path="x.pdf", paper=PaperSize("L", 50, 30))
    with pytest.raises(PrintError) as ei:
        qt_print.print_pdf_via_qt("FakeQueue", "x.pdf", spec)
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_quality_enum_mapping_exists():
    # 质量映射不变：枚举可被识别（纯映射，不触碰后端）
    assert PrintQuality.DRAFT.value == "draft"
    assert PrintQuality.HIGH.value == "high"
