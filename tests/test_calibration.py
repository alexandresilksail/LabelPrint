"""校准模块测试：测试页物理尺寸、偏移纯函数、静区/模块宽度/几何校验。

verify_scale 依赖并行 A 切片的 ``client.printing.pdf_render.detect_scale_mismatch``
（接口已冻结）。模块未就绪时相关用例 ``pytest.skip``，集成阶段全量重跑；
本文件不依赖 PySide6 / pywin32。
"""

import pytest
from pypdf import PdfReader

from client.printing.calibration import (
    apply_offset,
    check_label_geometry,
    check_module_width,
    check_quiet_zone,
    compute_shifted_position,
    render_test_page_pdf,
    verify_scale,
)
from client.printing.capabilities import (
    PaperSize,
    PrinterCapabilities,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec

PT_TO_MM = 25.4 / 72.0


def _page_mm(pdf_path):
    reader = PdfReader(str(pdf_path))
    page = reader.pages[0]
    mb = page.mediabox
    return float(mb.width) * PT_TO_MM, float(mb.height) * PT_TO_MM


# ---------------------------------------------------------------------------
# 测试页
# ---------------------------------------------------------------------------
class TestRenderTestPage:
    def test_a4_page_size_exact(self, tmp_path):
        paper = PaperSize("A4", 210.0, 297.0)
        out = tmp_path / "a4.pdf"
        meta = render_test_page_pdf(out, paper, dpi_hint=300)
        w, h = _page_mm(out)
        assert abs(w - 210.0) < 0.5
        assert abs(h - 297.0) < 0.5
        assert meta["pages"] == 1
        assert meta["width_mm"] == 210.0
        assert meta["height_mm"] == 297.0

    def test_label_size_page_size_exact(self, tmp_path):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        out = tmp_path / "label.pdf"
        render_test_page_pdf(out, paper)
        w, h = _page_mm(out)
        assert abs(w - 40.0) < 0.5
        assert abs(h - 25.0) < 0.5

    def test_text_contains_mm_and_spec(self, tmp_path):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        out = tmp_path / "label.pdf"
        render_test_page_pdf(out, paper)
        text = PdfReader(str(out)).pages[0].extract_text() or ""
        assert "mm" in text.lower()
        assert "40" in text and "25" in text


# ---------------------------------------------------------------------------
# 偏移纯函数
# ---------------------------------------------------------------------------
class TestOffset:
    def test_compute_shifted_position(self):
        assert compute_shifted_position(10.0, 20.0, 3.0, -4.0) == (13.0, 16.0)

    def test_apply_offset_returns_copy_unchanged_original(self):
        job = LabelJobSpec(pdf_path="a.pdf", paper=PaperSize("t", 40, 25))
        shifted = apply_offset(job, 1.5, -2.5)
        assert shifted is not job
        assert shifted.offset_x_mm == 1.5
        assert shifted.offset_y_mm == -2.5
        # 原任务不被修改
        assert job.offset_x_mm == 0.0
        assert job.offset_y_mm == 0.0
        # 其他字段保留
        assert shifted.pdf_path == "a.pdf"
        assert shifted.paper == job.paper


# ---------------------------------------------------------------------------
# 静区
# ---------------------------------------------------------------------------
class TestQuietZone:
    def test_exact_required_passes(self):
        assert check_quiet_zone(0.33, 11) is None

    def test_one_tenth_module_short_fails(self):
        issue = check_quiet_zone(0.33, 10.9)
        assert issue is not None
        assert issue.severity == "error"
        assert issue.code == PrintError.QUIET_ZONE_TOO_SMALL

    def test_required_modules_configurable(self):
        # 自定义要求 7 模块：7 通过、6 失败
        assert check_quiet_zone(0.33, 7, required_modules=7) is None
        issue = check_quiet_zone(0.33, 6, required_modules=7)
        assert issue is not None
        assert issue.code == PrintError.QUIET_ZONE_TOO_SMALL
        # 默认 11 要求下 10 失败
        assert check_quiet_zone(0.33, 10) is not None


# ---------------------------------------------------------------------------
# 模块宽度
# ---------------------------------------------------------------------------
class TestModuleWidth:
    @pytest.mark.parametrize("mw", [0.33, 0.264, 0.66])
    def test_in_range_passes(self, mw):
        assert check_module_width(mw) is None

    @pytest.mark.parametrize("mw", [0.2, 0.7, 0.2639, 0.661])
    def test_out_of_range_fails(self, mw):
        issue = check_module_width(mw)
        assert issue is not None
        assert issue.severity == "error"
        assert issue.code == PrintError.MODULE_WIDTH_OUT_OF_RANGE


# ---------------------------------------------------------------------------
# 几何校验
# ---------------------------------------------------------------------------
def _caps(**kw):
    defaults = dict(
        printer_name="DEV",
        max_printable_width_mm=100.0,
        max_printable_height_mm=100.0,
        supports_custom_paper=True,
        supports_true_size=False,
    )
    defaults.update(kw)
    return PrinterCapabilities(**defaults)


class TestLabelGeometry:
    def test_in_range_no_out_of_range_error(self):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        issues = check_label_geometry(paper, _caps())
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []
        # 未声明 1:1 支持 → warning
        assert any(i.code == "TRUE_SIZE_UNSUPPORTED" for i in issues)

    def test_oversized_paper_out_of_range_error(self):
        paper = PaperSize("大纸", 150.0, 100.0)
        issues = check_label_geometry(paper, _caps())
        errs = [i for i in issues if i.severity == "error"]
        assert len(errs) == 1
        assert errs[0].code == PrintError.PAPER_OUT_OF_RANGE

    def test_unsupported_paper_error(self):
        paper = PaperSize("异形", 80.0, 60.0)
        caps = _caps(supports_custom_paper=False, max_printable_width_mm=50.0,
                     max_printable_height_mm=50.0)
        issues = check_label_geometry(paper, caps)
        errs = [i for i in issues if i.severity == "error"]
        assert any(i.code == PrintError.PAPER_UNSUPPORTED for i in errs)

    def test_gap_without_sensor_warns(self):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        issues = check_label_geometry(paper, _caps(), label_gap_mm=3.0)
        warn = [i for i in issues if i.code == "GAP_SENSOR_UNSUPPORTED"]
        assert len(warn) == 1

    def test_gap_with_sensor_no_warning(self):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        caps = _caps()
        caps.supports_gap_sensor = True  # 能力模型可选声明
        issues = check_label_geometry(paper, caps, label_gap_mm=3.0)
        assert not any(i.code == "GAP_SENSOR_UNSUPPORTED" for i in issues)

    def test_zero_gap_no_warning(self):
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        issues = check_label_geometry(paper, _caps(), label_gap_mm=0.0)
        assert not any(i.code == "GAP_SENSOR_UNSUPPORTED" for i in issues)


# ---------------------------------------------------------------------------
# verify_scale（对接并行 A 切片 pdf_render，未就绪则 skip）
# ---------------------------------------------------------------------------
def _pdf_page(path, w_mm, h_mm):
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas

    c = rl_canvas.Canvas(str(path), pagesize=(w_mm * mm, h_mm * mm))
    c.drawString(10 * mm, 10 * mm, f"{w_mm}x{h_mm}")
    c.showPage()
    c.save()


class TestVerifyScale:
    def test_mismatch_reported_false(self, tmp_path):
        try:
            import client.printing.pdf_render  # noqa: F401
        except ImportError:
            pytest.skip("pdf_render 未就绪，集成阶段全量重跑")
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        pdf = tmp_path / "big.pdf"
        _pdf_page(pdf, 100.0, 100.0)
        report = verify_scale(str(pdf), paper)
        assert report.ok is False

    def test_match_reported_true(self, tmp_path):
        try:
            import client.printing.pdf_render  # noqa: F401
        except ImportError:
            pytest.skip("pdf_render 未就绪，集成阶段全量重跑")
        paper = PaperSize("标签 40x25", 40.0, 25.0)
        pdf = tmp_path / "ok.pdf"
        _pdf_page(pdf, 40.0, 25.0)
        report = verify_scale(str(pdf), paper)
        assert report.ok is True

    def test_missing_module_raises_unsupported_platform(self):
        try:
            import client.printing.pdf_render  # noqa: F401
            pytest.skip("pdf_render 已就绪，缺失分支不再适用")
        except ImportError:
            with pytest.raises(PrintError) as exc:
                verify_scale("x.pdf", PaperSize("t", 40.0, 25.0))
            assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
