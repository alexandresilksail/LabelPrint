"""打印中心纯决策逻辑测试（不依赖 Qt / pywin32）。

覆盖 ``client.ui.pages.print_export_logic``：
- merge_capabilities 合并 / 去重 / 空结果；
- pick_paper 命中 / 旋转命中 / 未命中自定义 + 能力判定；
- build_job_from_form 偏移应用 / 默认值 / 非法回退 / 份数钳制；
- summarize_capabilities；
- group_issues。
"""

from types import SimpleNamespace

import pytest

from client.printing.capabilities import (
    DriverInfo, JobOrientation, PaperSize, PrintQuality, PrinterCapabilities,
    PrinterStatus, Transport,
)
from client.printing.job import LabelJobSpec, ValidationIssue
from client.ui.pages.print_export_logic import (
    build_job_from_form,
    group_issues,
    merge_capabilities,
    merge_capabilities_list,
    pick_paper,
    paper_choice_note,
    summarize_capabilities,
)


def _win32_entry(**overrides) -> PrinterCapabilities:
    base = dict(
        printer_name="Zebra ZD420",
        driver=DriverInfo(driver_name="ZDesigner ZD420"),
        port_name="USB001",
        is_default=True,
        status=PrinterStatus(available=True),
        supported_papers=(PaperSize("40x25", 40.0, 25.0),),
    )
    base.update(overrides)
    return PrinterCapabilities(**base)


def _qt_entry(**overrides) -> PrinterCapabilities:
    base = dict(
        printer_name="zebra zd420",  # 同名不同大小写，应去重合并
        port_name=None,
        dpi=203,
        transport=Transport.UNKNOWN,
        notes="Qt 枚举",
    )
    base.update(overrides)
    return PrinterCapabilities(**base)


# ---------------------------------------------------------------------------
# merge_capabilities
# ---------------------------------------------------------------------------
class TestMergeCapabilities:
    def test_empty_returns_none(self):
        assert merge_capabilities([]) is None
        assert merge_capabilities(None) is None

    def test_single_entry_preserved(self):
        entry = _win32_entry()
        merged = merge_capabilities([entry])
        assert merged is not None
        assert merged.printer_name == "Zebra ZD420"
        assert merged.driver.driver_name == "ZDesigner ZD420"
        assert merged.port_name == "USB001"

    def test_win32_and_qt_dedupe_and_supplement(self):
        win = _win32_entry()
        qt = _qt_entry()
        merged = merge_capabilities([win, qt])
        assert merged is not None
        # 去重：同名只保留一条
        assert len(merge_capabilities_list([win, qt])) == 1
        # win32 的驱动/端口保留
        assert merged.driver.driver_name == "ZDesigner ZD420"
        assert merged.port_name == "USB001"
        # Qt 补充 win32 缺失的 dpi
        assert merged.dpi == 203
        # 默认标记 OR
        assert merged.is_default is True

    def test_default_chosen_when_present(self):
        non_default = _win32_entry(printer_name="Printer B", is_default=False,
                                   port_name="LPT1")
        default = _win32_entry(printer_name="Printer A", is_default=True,
                               port_name="USB002")
        merged = merge_capabilities([non_default, default])
        assert merged.printer_name == "Printer A"

    def test_papers_union_dedup(self):
        win = _win32_entry(supported_papers=(PaperSize("40x25", 40, 25),))
        qt = _qt_entry(supported_papers=(
            PaperSize("40x25", 40, 25), PaperSize("60x40", 60, 40)))
        merged = merge_capabilities([win, qt])
        names = {p.name for p in merged.supported_papers}
        assert names == {"40x25", "60x40"}


# ---------------------------------------------------------------------------
# pick_paper
# ---------------------------------------------------------------------------
class TestPickPaper:
    def test_hit_in_supported_list(self):
        caps = _win32_entry()
        paper = pick_paper(caps, 40.0, 25.0)
        assert paper.name == "40x25"
        assert paper.width_mm == pytest.approx(40.0)

    def test_rotated_hit(self):
        caps = _win32_entry()
        paper = pick_paper(caps, 25.0, 40.0)  # 旋转方向仍命中
        assert paper.width_mm == pytest.approx(40.0)
        assert paper.height_mm == pytest.approx(25.0)

    def test_miss_returns_custom_paper(self):
        caps = _win32_entry()
        paper = pick_paper(caps, 99.0, 99.0)
        assert "自定义" in paper.name
        assert paper.width_mm == pytest.approx(99.0)

    def test_none_caps_always_custom(self):
        paper = pick_paper(None, 50.0, 30.0)
        assert paper.width_mm == pytest.approx(50.0)
        assert "自定义" in paper.name

    def test_note_supports_custom_vs_not(self):
        caps_ok = _win32_entry(supports_custom_paper=True)
        caps_no = _win32_entry(supports_custom_paper=False)
        hit = pick_paper(caps_ok, 40, 25)
        assert "匹配" in paper_choice_note(caps_ok, hit)
        custom = pick_paper(caps_ok, 99, 99)
        assert "支持自定义纸张" in paper_choice_note(caps_ok, custom)
        custom_no = pick_paper(caps_no, 99, 99)
        assert "未声明支持自定义纸张" in paper_choice_note(caps_no, custom_no)


# ---------------------------------------------------------------------------
# build_job_from_form
# ---------------------------------------------------------------------------
class TestBuildJobFromForm:
    def _caps(self):
        return _win32_entry(dpi=203)

    def test_offset_applied_and_clamped(self):
        job = build_job_from_form(None, self._caps(), {
            "offset_x_mm": 9999.0, "offset_y_mm": -9999.0,
        })
        assert job.offset_x_mm == pytest.approx(500.0)
        assert job.offset_y_mm == pytest.approx(-500.0)

    def test_copies_clamped_and_invalid_fallback(self):
        high = build_job_from_form(None, self._caps(), {"copies": 5000})
        assert high.copies == 999
        low = build_job_from_form(None, self._caps(), {"copies": -3})
        assert low.copies == 1
        bad = build_job_from_form(None, self._caps(), {"copies": "abc"})
        assert bad.copies == 1

    def test_quality_and_orientation_invalid_fallback(self):
        job = build_job_from_form(None, self._caps(), {
            "quality": "ultra", "orientation": "sideways",
        })
        assert job.quality == PrintQuality.NORMAL
        assert job.orientation == JobOrientation.PORTRAIT

    def test_valid_quality_and_orientation_passed(self):
        job = build_job_from_form(None, self._caps(), {
            "quality": "high", "orientation": "landscape", "copies": 3,
        })
        assert job.quality == PrintQuality.HIGH
        assert job.orientation == JobOrientation.LANDSCAPE
        assert job.copies == 3

    def test_dpi_fallback_from_caps(self):
        job = build_job_from_form(None, self._caps(), {})
        assert job.dpi == 203
        explicit = build_job_from_form(None, self._caps(), {"dpi": 300})
        assert explicit.dpi == 300
        bad_dpi = build_job_from_form(None, self._caps(), {"dpi": -5})
        assert bad_dpi.dpi == 203  # 非法回退能力表

    def test_module_and_quiet_zone_defaults(self):
        job = build_job_from_form(None, self._caps(), {})
        assert job.module_mm == pytest.approx(0.33)
        # 静区默认 = 11 模块 × module_mm
        assert job.quiet_zone_mm == pytest.approx(11 * 0.33)

    def test_explicit_module_and_quiet(self):
        job = build_job_from_form(None, self._caps(), {
            "module_mm": 0.3, "quiet_zone_mm": 3.3,
        })
        assert job.module_mm == pytest.approx(0.3)
        assert job.quiet_zone_mm == pytest.approx(3.3)

    def test_paper_fallback_from_template(self):
        template = SimpleNamespace(
            template_name="A4 模板", paper_width=210.0, paper_height=297.0)
        job = build_job_from_form(template, self._caps(), {})
        assert job.paper is not None
        assert job.paper.width_mm == pytest.approx(210.0)
        assert job.paper.height_mm == pytest.approx(297.0)

    def test_form_paper_takes_precedence(self):
        template = SimpleNamespace(
            template_name="A4 模板", paper_width=210.0, paper_height=297.0)
        custom = PaperSize("标签 40x25", 40.0, 25.0)
        job = build_job_from_form(template, self._caps(), {"paper": custom})
        assert job.paper is custom

    def test_gap_non_negative(self):
        job = build_job_from_form(None, self._caps(), {"label_gap_mm": -2})
        assert job.label_gap_mm == 0.0

    def test_returns_label_job_spec(self):
        job = build_job_from_form(None, self._caps(), {})
        assert isinstance(job, LabelJobSpec)


# ---------------------------------------------------------------------------
# summarize_capabilities / group_issues
# ---------------------------------------------------------------------------
class TestSummaryAndGroup:
    def test_none_summary(self):
        assert "未检测到打印机" in summarize_capabilities(None)

    def test_summary_contains_driver_port_default_status(self):
        caps = _win32_entry()
        text = summarize_capabilities(caps)
        assert "驱动：ZDesigner ZD420" in text
        assert "端口：USB001" in text
        assert "默认打印机" in text
        assert "状态：就绪" in text

    def test_group_issues_split(self):
        issues = [
            ValidationIssue.error("E1", "e1"),
            ValidationIssue.warning("W1", "w1"),
            ValidationIssue.error("E2", "e2"),
            ValidationIssue.warning("W2", "w2"),
        ]
        errors, warnings = group_issues(issues)
        assert [i.code for i in errors] == ["E1", "E2"]
        assert [i.code for i in warnings] == ["W1", "W2"]

    def test_group_issues_empty(self):
        assert group_issues([]) == ([], [])
