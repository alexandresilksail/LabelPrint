"""PDF 渲染语义测试（切片验收）。

覆盖：
- 模板份数 1/2/3 × 任务份数 1/2/5（任务份数不进入 PDF，签名层面无 copies）
- 多编码跨页、页数 = ceil(expanded/slots_per_page)、dropped 计数
- MAX_SHEET_PAGES 页数封顶
- 校准偏移叠加（正/负/无偏移/与 template.offset 叠加）
- 静区模块数进入 fit 与编码
- target_module_mm 模块宽度生效（不超过容量上限）
- paper_override 放不下 → ValueError（"无法容纳"）；放得下 → pypdf 实测页面尺寸
- orientation_override=landscape 页面宽高交换
- render_pdf 新增可选校准参数且向后兼容
"""

import inspect

import pytest
from pypdf import PdfReader

from client.barcode.checkdigit import calculate_check_digit
from client.barcode.encoder import encode_with_quiet_zone, module_count
from client.printing.capabilities import JobOrientation, PaperSize
from client.printing.pdf import (
    MAX_SHEET_PAGES,
    render_label_sheet_pdf,
    render_pdf,
)
from client.printing.preview import fit_barcode
from client.printing.templates import LabelTemplate
from client.printing.units import pt_to_mm


def make_template(**kw):
    base = dict(
        template_name="语义测试", paper_width=210, paper_height=297,
        label_width=60, label_height=40, rows=3, columns=3,
    )
    base.update(kw)
    return LabelTemplate(**base)


def ean13(i):
    base = f"5901234123{i:02d}"[:12]
    return base + calculate_check_digit(base, "EAN-13")


# ---------- 份数规则：模板份数唯一展开点，任务份数不进入 PDF ----------

def test_signature_has_no_job_copies():
    """渲染函数签名层面不接受 copies 参数，任务份数无法进入 PDF。"""
    params = inspect.signature(render_label_sheet_pdf).parameters
    assert "copies" not in params


@pytest.mark.parametrize("template_copies", [1, 2, 3])
@pytest.mark.parametrize("job_copies", [1, 2, 5])
def test_copies_matrix_template_only(tmp_path, template_copies, job_copies):
    raw = [(ean13(i), "EAN-13") for i in range(3)]
    t = make_template(copies=template_copies)
    path = str(tmp_path / "sheet.pdf")
    meta = render_label_sheet_pdf(raw, t, path)

    # 任务份数（LabelJobSpec.copies=job_copies）只由 Qt/协议应用，
    # 渲染结果与其无关：labels_total 只等于 raw × template.copies
    assert meta["template_copies"] == template_copies
    assert meta["expanded_total"] == len(raw) * template_copies
    assert meta["labels_total"] == len(raw) * template_copies
    assert len(meta["slot_positions"]) == meta["labels_total"]
    # 9 slot/页，3×3=9 恰好一页
    assert meta["pages"] == 1
    assert meta["dropped"] == 0

    # 同一原始 items 换任意任务份数，渲染产物完全一致（仅证语义，job_copies
    # 从不传入渲染函数）
    meta2 = render_label_sheet_pdf(raw, make_template(copies=template_copies),
                                   str(tmp_path / "sheet2.pdf"))
    assert meta2["labels_total"] == meta["labels_total"]


# ---------- 多编码 + 跨页 ----------

def test_mixed_codes_paginate(tmp_path):
    items = []
    for i in range(20):
        if i % 2 == 0:
            items.append((ean13(i), "EAN-13"))
        else:
            items.append(("96385074", "EAN-8"))
    t = make_template()  # 9 slot/页
    meta = render_label_sheet_pdf(items, t, str(tmp_path / "p.pdf"))
    assert meta["expanded_total"] == 20
    assert meta["pages"] == -(-20 // 9)  # ceil(20/9) = 3
    assert meta["dropped"] == 0
    assert len(meta["slot_positions"]) == 20
    assert meta["labels_total"] == 20


# ---------- 页数上限 ----------

def test_page_cap_and_dropped_report(tmp_path):
    t = make_template(rows=1, columns=2)  # 2 slot/页
    n = MAX_SHEET_PAGES * 2 + 50
    items = [(ean13(i % 90), "EAN-13") for i in range(n)]
    meta = render_label_sheet_pdf(items, t, str(tmp_path / "cap.pdf"))
    assert meta["pages"] == MAX_SHEET_PAGES
    assert meta["labels_total"] == MAX_SHEET_PAGES * 2
    assert meta["dropped"] == 50
    assert any("超出" in w for w in meta["warnings"])


# ---------- 校准偏移叠加 ----------

def test_offset_positive_applied(tmp_path):
    t = make_template(rows=1, columns=1)
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], t, str(tmp_path / "o.pdf"),
        offset_x_mm=2.0)
    _, _, x, _, _ = meta["slot_positions"][0]
    assert abs(x - (5.0 + 2.0)) < 1e-6  # 左边距 5 + 校准 +2
    assert abs(meta["offset_x_mm"] - 2.0) < 1e-6


def test_offset_negative_applied(tmp_path):
    t = make_template(rows=1, columns=1)
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], t, str(tmp_path / "n.pdf"),
        offset_x_mm=-2.0)
    _, _, x, _, _ = meta["slot_positions"][0]
    assert abs(x - (5.0 - 2.0)) < 1e-6
    assert abs(meta["offset_x_mm"] - (-2.0)) < 1e-6


def test_no_offset_unchanged(tmp_path):
    t = make_template(rows=1, columns=1)
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], t, str(tmp_path / "z.pdf"))
    _, _, x, _, _ = meta["slot_positions"][0]
    assert abs(x - 5.0) < 1e-6
    assert meta["offset_x_mm"] == 0.0


def test_offset_stacks_template_offset(tmp_path):
    t = make_template(rows=1, columns=1, offset_x=1.0)
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], t, str(tmp_path / "s.pdf"),
        offset_x_mm=2.0)
    # final = template.offset(1) + 传入(2)；slot 已含 template.offset=1
    _, _, x, _, _ = meta["slot_positions"][0]
    assert abs(x - (5.0 + 1.0 + 2.0)) < 1e-6
    assert abs(meta["offset_x_mm"] - 3.0) < 1e-6


# ---------- 静区模块数 ----------

def test_quiet_zone_changes_total_modules_and_width():
    # 静区不同 → 含静区总模块数不同；自由适配时模块宽度随之不同
    f11 = fit_barcode(60, 40, "EAN-13", quiet=11, side_padding=2.0)
    f5 = fit_barcode(60, 40, "EAN-13", quiet=5, side_padding=2.0)
    assert f11["module_mm"] != f5["module_mm"]

    # 固定目标模块宽度时，内容流总宽度随静区模块数线性变化
    g11 = fit_barcode(60, 40, "EAN-13", quiet=11, side_padding=2.0,
                      target_module_mm=0.3)
    g5 = fit_barcode(60, 40, "EAN-13", quiet=5, side_padding=2.0,
                     target_module_mm=0.3)
    assert g11["barcode_w_mm"] != g5["barcode_w_mm"]
    assert g11["barcode_w_mm"] == \
        (module_count("EAN-13") + 2 * 11) * 0.3
    assert g5["barcode_w_mm"] == \
        (module_count("EAN-13") + 2 * 5) * 0.3

    v = ean13(1)
    assert len(encode_with_quiet_zone(v, "EAN-13", 11)) == \
        module_count("EAN-13") + 2 * 11
    assert len(encode_with_quiet_zone(v, "EAN-13", 5)) == \
        module_count("EAN-13") + 2 * 5


def test_quiet_passes_through_render(tmp_path):
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], make_template(rows=1, columns=1),
        str(tmp_path / "q.pdf"), quiet=5)
    assert meta["labels_total"] == 1


# ---------- 目标模块宽度 ----------

def test_target_module_mm_respected(tmp_path):
    f = fit_barcode(60, 40, "EAN-13", quiet=11, side_padding=2.0,
                    target_module_mm=0.4)
    assert abs(f["module_mm"] - 0.4) < 1e-9
    # 超出标签容量上限时等比压到最大值，不越界
    avail_w = 60 - 2 * 2.0
    cap = fit_barcode(60, 40, "EAN-13", quiet=11, side_padding=2.0,
                      target_module_mm=5.0)
    assert cap["module_mm"] <= avail_w / (module_count("EAN-13") + 2 * 11) + 1e-9

    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], make_template(rows=1, columns=1),
        str(tmp_path / "m.pdf"), target_module_mm=0.4)
    assert meta["labels_total"] == 1


# ---------- paper_override / orientation_override ----------

def test_small_paper_rejected(tmp_path):
    t = make_template()  # 网格实际占用约 191×131mm
    small = PaperSize("小纸", 50.0, 30.0)
    with pytest.raises(ValueError, match="无法容纳"):
        render_label_sheet_pdf(
            [(ean13(1), "EAN-13")], t, str(tmp_path / "x.pdf"),
            paper_override=small)


def test_paper_override_fits_measured_by_pypdf(tmp_path):
    t = make_template(rows=2, columns=2)  # 网格占用 128×88mm
    paper = PaperSize("测试纸", 150.0, 120.0)
    meta = render_label_sheet_pdf(
        [(ean13(i), "EAN-13") for i in range(2)], t,
        str(tmp_path / "ok.pdf"), paper_override=paper)
    assert abs(meta["paper_width_mm"] - 150.0) < 1e-6
    assert abs(meta["paper_height_mm"] - 120.0) < 1e-6

    box = PdfReader(str(tmp_path / "ok.pdf")).pages[0].mediabox
    assert abs(pt_to_mm(float(box.width)) - 150.0) < 0.5
    assert abs(pt_to_mm(float(box.height)) - 120.0) < 0.5


def test_landscape_override_swaps_page(tmp_path):
    t = make_template(rows=2, columns=2)  # A4 210×297，网格 128×88 双向放得下
    meta = render_label_sheet_pdf(
        [(ean13(i), "EAN-13") for i in range(2)], t,
        str(tmp_path / "l.pdf"), orientation_override=JobOrientation.LANDSCAPE)
    assert abs(meta["paper_width_mm"] - 297.0) < 1e-6
    assert abs(meta["paper_height_mm"] - 210.0) < 1e-6
    box = PdfReader(str(tmp_path / "l.pdf")).pages[0].mediabox
    assert abs(pt_to_mm(float(box.width)) - 297.0) < 0.5
    assert abs(pt_to_mm(float(box.height)) - 210.0) < 0.5


def test_default_page_unchanged(tmp_path):
    meta = render_label_sheet_pdf(
        [(ean13(1), "EAN-13")], make_template(),
        str(tmp_path / "d.pdf"))
    assert meta["paper_width_mm"] == 210.0
    assert meta["paper_height_mm"] == 297.0


# ---------- render_pdf 新增校准参数（向后兼容） ----------

def test_render_pdf_calibration_params(tmp_path):
    params = inspect.signature(render_pdf).parameters
    for name in ("offset_x_mm", "offset_y_mm", "quiet", "target_module_mm"):
        assert name in params

    meta = render_pdf(ean13(1), "EAN-13", str(tmp_path / "s.pdf"),
                      offset_x_mm=1.0, offset_y_mm=2.0,
                      target_module_mm=0.4, quiet=11)
    assert abs(meta["module_mm"] - 0.4) < 1e-9
    assert meta["offset_x_mm"] == 1.0
    assert meta["offset_y_mm"] == 2.0

    legacy = render_pdf("5901234123457", "EAN-13", str(tmp_path / "b.pdf"))
    assert legacy["offset_x_mm"] == 0.0
    assert legacy["offset_y_mm"] == 0.0
