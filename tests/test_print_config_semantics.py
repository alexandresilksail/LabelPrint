"""P1「通用 PDF 打印配置传递」语义测试（第二轮审计）。

覆盖冻结语义：
- 静区单位唯一换算（mm ↔ 模块数，units.py），非法输入显式抛错；
- check_quiet_zone_mm 毫米版本校验，既有 check_quiet_zone 行为不变；
- 配置 → 渲染参数映射（resolve_render_params）：mm 换算、module 解析、
  gap 直通、硬件参数列入 unapplied；
- 静区 mm 真实进入 fit_barcode/encode，meta 回执字段正确，未传默认 11；
- 标签物理间隙 label_gap_mm 不进条码布局：gap=3 与 gap=0 渲染几何一致；
- 硬件参数（走纸/间隙传感/打印浓度）如实标注"不由软件直接控制"；
- 全链路：UI 配置 dict → build_job_from_form → LabelJobSpec →
  resolve_render_params → render_label_sheet_pdf → pypdf 测量 + meta 断言。

本文件不依赖 PySide6 / pywin32；既有测试断言一律未改。
"""

import pytest
from pypdf import PdfReader

from client.barcode.checkdigit import calculate_check_digit
from client.barcode.encoder import encode_with_quiet_zone, module_count
from client.printing.calibration import (
    check_quiet_zone,
    check_quiet_zone_mm,
)
from client.printing.capabilities import PaperSize, PrinterCapabilities
from client.printing.config_store import PrinterPrintConfig
from client.printing.pdf import render_label_sheet_pdf
from client.printing.preview import fit_barcode
from client.printing.templates import LabelTemplate
from client.printing.units import (
    quiet_modules_to_mm,
    quiet_zone_mm_to_modules,
)
from client.printing.units import pt_to_mm
from client.ui.pages.print_export_logic import (
    build_job_from_form,
    hardware_controlled_notice,
    resolve_render_params,
)


def ean(i):
    base = f"5901234123{i:02d}"[:12]
    return base + calculate_check_digit(base, "EAN-13")


def make_template(**kw):
    base = dict(
        template_name="语义测试", paper_width=80, paper_height=60,
        label_width=60, label_height=40, rows=1, columns=1,
    )
    base.update(kw)
    return LabelTemplate(**base)


def _caps(**over):
    base = dict(printer_name="DEV", dpi=203,
                supported_papers=(PaperSize("60x40", 60.0, 40.0),))
    base.update(over)
    return PrinterCapabilities(**base)


# ---------------------------------------------------------------------------
# 1. 单位换算：mm ↔ 模块数（唯一入口 units.py）
# ---------------------------------------------------------------------------
class TestQuietZoneUnits:
    def test_mm_to_modules_exact(self):
        # 3.3mm / 0.33mm = 10 模块
        assert quiet_zone_mm_to_modules(3.3, 0.33) == 10
        assert quiet_zone_mm_to_modules(3.63, 0.33) == 11

    def test_round_boundaries(self):
        # 10.49 模块 → 10；11.51 模块 → 12（四舍五入，非平局）
        assert quiet_zone_mm_to_modules(10.49 * 0.33, 0.33) == 10
        assert quiet_zone_mm_to_modules(11.51 * 0.33, 0.33) == 12

    def test_non_positive_module_raises(self):
        for bad in (0.0, -0.33):
            with pytest.raises(ValueError):
                quiet_zone_mm_to_modules(3.3, bad)

    def test_negative_quiet_raises(self):
        with pytest.raises(ValueError):
            quiet_zone_mm_to_modules(-1.0, 0.33)
        with pytest.raises(ValueError):
            quiet_zone_mm_to_modules(None, 0.33)

    def test_inverse_conversion(self):
        assert quiet_modules_to_mm(10, 0.33) == pytest.approx(3.3)
        # 互逆：mm → modules → mm 稳定
        mods = quiet_zone_mm_to_modules(3.3, 0.33)
        assert quiet_modules_to_mm(mods, 0.33) == pytest.approx(3.3)

    def test_inverse_non_positive_module_raises(self):
        with pytest.raises(ValueError):
            quiet_modules_to_mm(10, 0.0)


# ---------------------------------------------------------------------------
# 2. check_quiet_zone_mm（既有 check_quiet_zone 行为不变）
# ---------------------------------------------------------------------------
class TestCheckQuietZoneMm:
    def test_exact_default_passes(self):
        # 3.63mm = 11 模块 × 0.33，满足默认 11 模块要求
        assert check_quiet_zone_mm(3.63, 0.33) is None

    def test_short_quiet_mm_fails(self):
        issue = check_quiet_zone_mm(3.3, 0.33)  # 10 模块 < 11
        assert issue is not None
        assert issue.severity == "error"
        assert issue.code == "QUIET_ZONE_TOO_SMALL"

    def test_existing_module_signature_unchanged(self):
        # 既有模块数版本行为不变：11 通过、10 失败
        assert check_quiet_zone(0.33, 11) is None
        assert check_quiet_zone(0.33, 10) is not None

    def test_non_positive_module_raises(self):
        with pytest.raises(ValueError):
            check_quiet_zone_mm(3.3, 0.0)


# ---------------------------------------------------------------------------
# 3. 配置 → 渲染参数映射 resolve_render_params
# ---------------------------------------------------------------------------
class TestResolveRenderParams:
    def test_quiet_mm_converted_to_modules(self):
        params = resolve_render_params(
            {"quiet_zone_mm": 3.3, "module_mm": 0.33, "label_gap_mm": 3.0})
        assert params["quiet_zone_mm"] == pytest.approx(3.3)
        assert params["quiet_modules"] == 10
        assert params["target_module_mm"] == pytest.approx(0.33)

    def test_gap_passthrough_not_quiet(self):
        params = resolve_render_params(
            {"quiet_zone_mm": 3.3, "module_mm": 0.33, "label_gap_mm": 3.0})
        assert params["label_gap_mm"] == pytest.approx(3.0)
        assert params["label_gap_mm_passthrough"] is True

    def test_missing_quiet_defaults_11(self):
        params = resolve_render_params({"module_mm": 0.33})
        assert params["quiet_modules"] == 11
        assert params["quiet_zone_mm"] is None

    def test_printer_config_object_supported(self):
        cfg = PrinterPrintConfig(
            printer_name="P", quiet_zone_mm=3.63, module_mm=0.33,
            label_gap_mm=2.5)
        params = resolve_render_params(cfg)
        assert params["quiet_modules"] == 11
        assert params["label_gap_mm"] == pytest.approx(2.5)

    def test_hardware_params_listed_unapplied(self):
        params = resolve_render_params({
            "quiet_zone_mm": 3.3, "module_mm": 0.33,
            "print_darkness": 12, "feed_mm": 2.0, "gap_sensor": "on",
        })
        blob = "\n".join(params["unapplied_params"])
        assert "打印浓度" in blob
        assert "走纸" in blob
        assert "间隙传感" in blob
        assert "不由软件直接控制" in blob

    def test_no_hardware_no_unapplied(self):
        params = resolve_render_params({"quiet_zone_mm": 3.3})
        assert params["unapplied_params"] == []


# ---------------------------------------------------------------------------
# 4. 硬件参数诚信提示 hardware_controlled_notice
# ---------------------------------------------------------------------------
class TestHardwareNotice:
    def test_notice_contains_not_controlled(self):
        text = "\n".join(hardware_controlled_notice(_caps()))
        assert "不由软件直接控制" in text
        assert "走纸" in text
        assert "浓度" in text

    def test_no_gap_sensor_extra_notice(self):
        notices = hardware_controlled_notice(_caps(supports_gap_sensor=False))
        assert any("不支持间隙传感" in n for n in notices)

    def test_gap_sensor_supported_no_extra(self):
        notices = hardware_controlled_notice(
            _caps(supports_gap_sensor=True))
        assert not any("不支持间隙传感" in n for n in notices)

    def test_none_caps_still_honest(self):
        notices = hardware_controlled_notice(None)
        assert any("不由软件直接控制" in n for n in notices)
        assert any("不支持间隙传感" in n for n in notices)


# ---------------------------------------------------------------------------
# 5. 静区 mm 真实进入 fit/encode；meta 回执正确；缺省默认 11
# ---------------------------------------------------------------------------
class TestQuietAppliedInPdf:
    def test_fit_width_diff_equals_two_delta_quiet_modules(self):
        # 固定模块宽 0.33：静区 11 → 10，含静区宽度差 = 2×(11-10)×0.33
        f11 = fit_barcode(60, 40, "EAN-13", quiet=11, side_padding=2.0,
                          target_module_mm=0.33)
        f10 = fit_barcode(60, 40, "EAN-13", quiet=10, side_padding=2.0,
                          target_module_mm=0.33)
        assert f11["barcode_w_mm"] - f10["barcode_w_mm"] == \
            pytest.approx(2 * (11 - 10) * 0.33)

    def test_encode_length_changes_with_quiet(self):
        v = ean(1)
        n11 = len(encode_with_quiet_zone(v, "EAN-13", 11))
        n10 = len(encode_with_quiet_zone(v, "EAN-13", 10))
        assert n11 - n10 == 2
        assert n11 == module_count("EAN-13") + 2 * 11

    def test_quiet_zone_mm_meta_applied(self, tmp_path):
        paper = PaperSize("测试纸", 80.0, 60.0)
        meta = render_label_sheet_pdf(
            [(ean(1), "EAN-13")], make_template(),
            str(tmp_path / "q.pdf"),
            quiet_zone_mm=3.3, target_module_mm=0.33,
            paper_override=paper)
        assert meta["quiet_modules_applied"] == 10
        assert meta["quiet_zone_mm_applied"] == pytest.approx(3.3, abs=1e-6)
        assert meta["module_mm_applied"] == pytest.approx(0.33, abs=1e-6)

    def test_default_eleven_modules_when_absent(self, tmp_path):
        paper = PaperSize("测试纸", 80.0, 60.0)
        meta = render_label_sheet_pdf(
            [(ean(1), "EAN-13")], make_template(),
            str(tmp_path / "d.pdf"), paper_override=paper)
        assert meta["quiet_modules_applied"] == 11

    def test_hardware_params_in_unapplied_meta(self, tmp_path):
        paper = PaperSize("测试纸", 80.0, 60.0)
        meta = render_label_sheet_pdf(
            [(ean(1), "EAN-13")], make_template(),
            str(tmp_path / "h.pdf"), paper_override=paper,
            hardware_params={"feed_mm": 2.0, "print_darkness": 10})
        blob = "\n".join(meta["unapplied_params"])
        assert "feed_mm" in blob
        assert "print_darkness" in blob
        assert "不由软件直接控制" in blob


# ---------------------------------------------------------------------------
# 6. 间隙不混用：gap=3 与 gap=0 条码布局完全一致
# ---------------------------------------------------------------------------
class TestGapNotMixed:
    def test_render_geometry_identical_regardless_of_gap(self, tmp_path):
        paper = PaperSize("测试纸", 80.0, 60.0)
        common = dict(quiet_zone_mm=3.3, target_module_mm=0.33,
                      paper_override=paper)
        m3 = render_label_sheet_pdf(
            [(ean(1), "EAN-13")], make_template(),
            str(tmp_path / "g3.pdf"), label_gap_mm=3.0, **common)
        m0 = render_label_sheet_pdf(
            [(ean(1), "EAN-13")], make_template(),
            str(tmp_path / "g0.pdf"), label_gap_mm=0.0, **common)

        # 槽位位置完全一致
        assert m3["slot_positions"] == m0["slot_positions"]
        # 静区/模块宽/条码布局一致（间隙未进布局）
        assert m3["quiet_modules_applied"] == m0["quiet_modules_applied"]
        assert m3["module_mm_applied"] == m0["module_mm_applied"]
        assert m3["quiet_zone_mm_applied"] == \
            pytest.approx(m0["quiet_zone_mm_applied"])
        # 仅直通字段不同
        assert m3["label_gap_mm_passthrough"] == pytest.approx(3.0)
        assert m0["label_gap_mm_passthrough"] == 0.0
        assert "未参与条码" in m3["label_gap_mm_note"] or \
            "未参与" in m3["label_gap_mm_note"]

    def test_fit_barcode_never_receives_gap(self):
        # fit_barcode 签名无 gap 参数；间隙无法进入条码几何
        import inspect
        params = inspect.signature(fit_barcode).parameters
        assert "label_gap_mm" not in params
        assert "gap" not in params


# ---------------------------------------------------------------------------
# 7. 全链路：配置 dict → build_job_from_form → 渲染参数 → PDF → pypdf 测量
# ---------------------------------------------------------------------------
class TestFullChain:
    def test_config_flows_into_job_and_pdf(self, tmp_path):
        tpl = make_template()
        form = {
            "paper": PaperSize("测试纸", 80.0, 60.0),
            "quiet_zone_mm": 3.3,
            "module_mm": 0.33,
            "label_gap_mm": 3.0,
            "copies": 2,
            "offset_x_mm": 1.0,
            "offset_y_mm": -1.0,
        }
        items = [(ean(1), "EAN-13")]
        job = build_job_from_form(tpl, _caps(), form,
                                  items=items, template=tpl)

        # LabelJobSpec：mm 配置保留，内容模型静区已换算为 10 模块
        assert job.quiet_zone_mm == pytest.approx(3.3)
        assert job.module_mm == pytest.approx(0.33)
        assert job.label_gap_mm == pytest.approx(3.0)
        assert job.label_content is not None
        assert job.label_content.quiet_modules == 10
        assert job.label_content.module_mm == pytest.approx(0.33)
        assert job.label_content.label_gap_mm == pytest.approx(3.0)
        # 任务份数不进内容模型
        assert job.copies == 2
        assert job.label_content.template_copies == 1

        # 配置 → 渲染参数
        params = resolve_render_params(job, caps=_caps())
        assert params["quiet_modules"] == 10

        # 渲染：配置真实生效
        pdf_path = str(tmp_path / "chain.pdf")
        meta = render_label_sheet_pdf(
            items, tpl, pdf_path,
            quiet_zone_mm=params["quiet_zone_mm"],
            target_module_mm=params["target_module_mm"],
            label_gap_mm=params["label_gap_mm"],
            offset_x_mm=job.offset_x_mm,
            offset_y_mm=job.offset_y_mm,
            paper_override=job.paper,
            hardware_params={"print_darkness": 11})

        # meta 回执
        assert meta["quiet_modules_applied"] == 10
        assert meta["module_mm_applied"] == pytest.approx(0.33)
        assert meta["label_gap_mm_passthrough"] == pytest.approx(3.0)
        assert any("打印浓度" in u for u in meta["unapplied_params"])

        # pypdf 实测页面物理尺寸 == 所选纸张 80×60mm
        box = PdfReader(pdf_path).pages[0].mediabox
        assert abs(pt_to_mm(float(box.width)) - 80.0) < 0.5
        assert abs(pt_to_mm(float(box.height)) - 60.0) < 0.5
        assert abs(meta["paper_width_mm"] - 80.0) < 1e-6
        assert abs(meta["paper_height_mm"] - 60.0) < 1e-6

    def test_job_defaults_when_form_empty(self):
        job = build_job_from_form(None, _caps(), {})
        # 缺省静区 = 11 × 0.33；未传 module → 标称 0.33（既有默认不变）
        assert job.module_mm == pytest.approx(0.33)
        assert job.quiet_zone_mm == pytest.approx(11 * 0.33)
