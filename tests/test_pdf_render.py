"""pdf_render 测试：真实 reportlab 生成已知物理尺寸 PDF，pypdf 测量与缩放检测。

不依赖图像识别：尺寸即物理真相。运行方式（仓库根目录）：
    python3 -m pytest tests/test_pdf_render.py -q
"""

import os
import tempfile

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as rl_canvas

from client.printing.capabilities import PaperSize
from client.printing.errors import PrintError
from client.printing.pdf_render import (
    detect_scale_mismatch,
    measure_pdf_pages,
    rasterize_preview,
)


def _make_pdf(path, w_mm, h_mm, pages=1, rotate=None):
    """用 reportlab 生成精确毫米尺寸的 PDF；rotate 写入 /Rotate。"""
    c = rl_canvas.Canvas(path, pagesize=(w_mm * mm, h_mm * mm))
    for _ in range(pages):
        c.drawString(5 * mm, 5 * mm, "label")
        c.showPage()
    c.save()
    if rotate:
        reader = PdfReader(path)
        writer = PdfWriter()
        for page in reader.pages:
            page.rotation = rotate
            writer.add_page(page)
        with open(path, "wb") as f:
            writer.write(f)


@pytest.fixture()
def tmp_pdf():
    td = tempfile.TemporaryDirectory()
    yield td.name
    td.cleanup()


def test_measure_40x25_label(tmp_pdf):
    path = os.path.join(tmp_pdf, "label.pdf")
    _make_pdf(path, 40.0, 25.0)
    pages = measure_pdf_pages(path)
    assert len(pages) == 1
    assert pages[0]["page"] == 1
    assert abs(pages[0]["width_mm"] - 40.0) < 0.2
    assert abs(pages[0]["height_mm"] - 25.0) < 0.2


def test_measure_a4(tmp_pdf):
    path = os.path.join(tmp_pdf, "a4.pdf")
    _make_pdf(path, 210.0, 297.0)
    pages = measure_pdf_pages(path)
    assert abs(pages[0]["width_mm"] - 210.0) < 0.3
    assert abs(pages[0]["height_mm"] - 297.0) < 0.3


def test_rotation_90_swaps_dimensions(tmp_pdf):
    path = os.path.join(tmp_pdf, "rot.pdf")
    _make_pdf(path, 40.0, 25.0, rotate=90)
    pages = measure_pdf_pages(path)
    # 旋转 90° 后铺纸宽高互换
    assert abs(pages[0]["width_mm"] - 25.0) < 0.2
    assert abs(pages[0]["height_mm"] - 40.0) < 0.2
    assert pages[0]["rotation"] == 90


def test_mismatch_detected_100x100_vs_50x30(tmp_pdf):
    path = os.path.join(tmp_pdf, "big.pdf")
    _make_pdf(path, 100.0, 100.0)
    report = detect_scale_mismatch(
        path, PaperSize("50x30", 50.0, 30.0), tolerance_mm=0.5
    )
    assert report.ok is False
    assert report.warnings
    assert any("非预期缩放" in w for w in report.warnings)


def test_match_size_ok(tmp_pdf):
    path = os.path.join(tmp_pdf, "match.pdf")
    _make_pdf(path, 50.0, 30.0)
    report = detect_scale_mismatch(
        path, PaperSize("50x30", 50.0, 30.0), tolerance_mm=0.5
    )
    assert report.ok is True
    assert not any("非预期缩放" in w for w in report.warnings)


def test_multipage_uses_first_page_warning(tmp_pdf):
    path = os.path.join(tmp_pdf, "multi.pdf")
    _make_pdf(path, 50.0, 30.0, pages=3)
    report = detect_scale_mismatch(
        path, PaperSize("50x30", 50.0, 30.0), tolerance_mm=0.5
    )
    assert report.ok is True
    assert any("首页" in w and "3 页" in w for w in report.warnings)


def test_rasterize_unsupported_on_linux(tmp_pdf):
    path = os.path.join(tmp_pdf, "x.pdf")
    _make_pdf(path, 50.0, 30.0)
    with pytest.raises(PrintError) as exc:
        rasterize_preview(path, dpi=150, max_pages=1)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
