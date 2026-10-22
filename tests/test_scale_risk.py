"""缩放风险严格分级测试（纯函数 + 适配器/qt 对齐，不触碰硬件）。

覆盖：
- classify_scale_risk 分级矩阵：匹配 / 容差内偏差 / 超容差 block(warn) /
  supports_true_size=False / 未知 None；
- 边界：A4 210×297、40×25 标签、自定义 57×40、横纵互换、容差
  0.1/0.5/2.0、DPI 203/300/600/None（不改变分级）；
- universal.validate_job：block→error 阻止提交，warn→warning 不阻止；
- qt_print.resolve_effective_page 三态保持，转换注记含诚信提示；
- 诚信红线：任何 message 不得出现“已应用”。

运行方式（仓库根目录）：
    python3 -m pytest tests/test_scale_risk.py -q
"""

import pytest

from client.printing import qt_print, scale_policy as sp
from client.printing.adapters._common import has_error
from client.printing.adapters.universal import UniversalWindowsAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec, ScaleReport


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _caps(true_size):
    """构造仅带 supports_true_size 声明的能力（None 表示未知）。"""
    return PrinterCapabilities(
        printer_name="P", transport=Transport.LOCAL,
        protocol=Protocol.UNIVERSAL, supports_true_size=true_size,
    )


def _paper(w, h, name="p"):
    return PaperSize(name, w, h)


# ---------------------------------------------------------------------------
# 分级矩阵：尺寸匹配 + 设备能力
# ---------------------------------------------------------------------------
def test_match_with_true_size_declared_is_ok():
    risk = sp.classify_scale_risk(
        50.0, 30.0, _paper(50, 30), _caps(True))
    assert risk.level == sp.RISK_OK
    assert risk.reasons == ()
    assert not risk.warned and not risk.blocked


def test_match_true_size_not_declared_warns_with_honesty():
    risk = sp.classify_scale_risk(
        50.0, 30.0, _paper(50, 30), _caps(False))
    assert risk.level == sp.RISK_WARN
    assert sp.REASON_TRUE_SIZE_NOT_DECLARED in risk.reasons
    # 诚信：页面尺寸正确 ≠ 物理输出尺寸正确；不宣称控制驱动缩放
    assert "不等于物理标签输出尺寸" in risk.message
    assert "第三方驱动缩放不受本软件控制" in risk.message


def test_match_true_size_unknown_warns_unconfirmed():
    risk = sp.classify_scale_risk(
        50.0, 30.0, _paper(50, 30), _caps(None))
    assert risk.level == sp.RISK_WARN
    assert sp.REASON_TRUE_SIZE_UNKNOWN in risk.reasons
    assert "目标物理尺寸无法由软件确认" in risk.message


def test_missing_caps_treated_unknown_warn():
    risk = sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), None)
    assert risk.level == sp.RISK_WARN
    assert sp.REASON_TRUE_SIZE_UNKNOWN in risk.reasons


# ---------------------------------------------------------------------------
# 尺寸超容差：未许可 block / 已确认转换 warn
# ---------------------------------------------------------------------------
def test_mismatch_without_conversion_is_block():
    risk = sp.classify_scale_risk(
        100.0, 100.0, _paper(50, 30), _caps(True),
        allow_paper_conversion=False, tolerance_mm=0.5)
    assert risk.blocked
    assert risk.code == "PAPER_SIZE_MISMATCH"
    assert sp.REASON_PAPER_SIZE_MISMATCH in risk.reasons


def test_mismatch_with_conversion_is_warn_1to1():
    risk = sp.classify_scale_risk(
        100.0, 100.0, _paper(50, 30), _caps(True),
        allow_paper_conversion=True, tolerance_mm=0.5)
    assert risk.warned
    assert risk.code == "PAPER_SIZE_MISMATCH"
    assert sp.REASON_PAPER_CONVERTED in risk.reasons
    assert "按 PDF 实际尺寸" in risk.message
    assert "1:1 输出" in risk.message
    assert "已按用户确认转换" in risk.message


# ---------------------------------------------------------------------------
# 边界：纸张规格 / 方向互换 / 容差
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("pw,ph,pdfw,pdfh", [
    (210.0, 297.0, 210.0, 297.0),          # A4 纵向
    (210.0, 297.0, 297.0, 210.0),          # A4 横纵互换
    (40.0, 25.0, 40.0, 25.0),              # 小标签
    (57.0, 40.0, 57.0, 40.0),              # 自定义
])
def test_various_paper_sizes_match_when_true_size_declared(
        pw, ph, pdfw, pdfh):
    risk = sp.classify_scale_risk(
        pdfw, pdfh, _paper(pw, ph), _caps(True))
    assert risk.level == sp.RISK_OK


def test_within_tolerance_small_deviation_matches():
    # 偏差 0.4mm < tol 0.5 → 容差内匹配（能力声明 true_size=True → ok）
    risk = sp.classify_scale_risk(
        50.4, 30.0, _paper(50, 30), _caps(True), tolerance_mm=0.5)
    assert risk.level == sp.RISK_OK


def test_tolerance_boundary_inclusive():
    # 偏差恰好等于容差（<=）→ 匹配
    risk = sp.classify_scale_risk(
        50.5, 30.0, _paper(50, 30), _caps(True), tolerance_mm=0.5)
    assert risk.level == sp.RISK_OK


@pytest.mark.parametrize("tol,dev,expect_match", [
    (0.1, 0.05, True),    # 0.05 <= 0.1 匹配
    (0.1, 0.2, False),    # 0.2 > 0.1 超容差
    (0.5, 0.6, False),
    (2.0, 1.5, True),     # 大容差宽松匹配
])
def test_tolerance_levels(tol, dev, expect_match):
    risk = sp.classify_scale_risk(
        50.0 + dev, 30.0, _paper(50, 30), _caps(True), tolerance_mm=tol)
    if expect_match:
        assert risk.level == sp.RISK_OK
    else:
        assert risk.blocked


# ---------------------------------------------------------------------------
# DPI / 可打印边距不改变分级（另由能力表/公共校验处理）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("dpi", [203, 300, 600, None])
def test_dpi_does_not_change_classification(dpi):
    caps = PrinterCapabilities(
        printer_name="P", transport=Transport.LOCAL,
        protocol=Protocol.UNIVERSAL, supports_true_size=True, dpi=dpi)
    risk = sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), caps)
    assert risk.level == sp.RISK_OK


def test_printable_margins_do_not_affect_policy_level():
    # 边距 0 与非 0 的 effective 页：本策略只核对物理页面尺寸，等级不变
    caps = PrinterCapabilities(
        printer_name="P", transport=Transport.LOCAL,
        protocol=Protocol.UNIVERSAL, supports_true_size=True,
        max_printable_width_mm=100.0, max_printable_height_mm=100.0)
    r0 = sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), caps)
    caps2 = PrinterCapabilities(
        printer_name="P", transport=Transport.LOCAL,
        protocol=Protocol.UNIVERSAL, supports_true_size=True,
        max_printable_width_mm=None, max_printable_height_mm=None)
    r1 = sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), caps2)
    assert r0.level == r1.level == sp.RISK_OK


# ---------------------------------------------------------------------------
# 诚信红线：message 不得出现“已应用”
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("risk", [
    sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), _caps(True)),
    sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), _caps(False)),
    sp.classify_scale_risk(50.0, 30.0, _paper(50, 30), _caps(None)),
    sp.classify_scale_risk(100.0, 100.0, _paper(50, 30), _caps(True),
                           allow_paper_conversion=False),
    sp.classify_scale_risk(100.0, 100.0, _paper(50, 30), _caps(True),
                           allow_paper_conversion=True),
])
def test_never_says_applied(risk):
    assert "已应用" not in risk.message


# ---------------------------------------------------------------------------
# universal.validate_job：block→error 阻止提交；warn→warning 不阻止
# ---------------------------------------------------------------------------
def _adapter(checker):
    return UniversalWindowsAdapter(
        PrinterCapabilities(
            printer_name="Q", transport=Transport.LOCAL,
            protocol=Protocol.UNIVERSAL, dpi=203,
            supported_papers=(_paper(50, 30, "50x30"),),
            max_printable_width_mm=100.0, max_printable_height_mm=100.0),
        scale_checker=checker,
    )


def _report(aw, ah, ok, warnings=None, tol=0.5):
    return ScaleReport(
        expected_width_mm=50.0, expected_height_mm=30.0,
        actual_width_mm=aw, actual_height_mm=ah,
        tolerance_mm=tol, ok=ok, warnings=warnings or [])


def _job(**over):
    base = dict(pdf_path="label.pdf", paper=_paper(50, 30), copies=1, dpi=203)
    base.update(over)
    return LabelJobSpec(**base)


def test_validate_block_mismatch_is_error_issue():
    adapter = _adapter(lambda *a: _report(100.0, 100.0, ok=False))
    issues = adapter.validate_job(_job(allow_paper_conversion=False))
    errs = [i for i in issues if i.severity == "error"]
    assert len(errs) == 1
    assert errs[0].code == PrintError.PAPER_SIZE_MISMATCH
    assert has_error(issues)


def test_validate_conversion_warn_not_blocking():
    adapter = _adapter(lambda *a: _report(100.0, 100.0, ok=False))
    issues = adapter.validate_job(_job(allow_paper_conversion=True))
    size = [i for i in issues if i.code == PrintError.PAPER_SIZE_MISMATCH]
    assert len(size) == 1
    assert size[0].severity == "warning"
    assert "已按用户确认转换" in size[0].message
    assert not has_error(issues)


def test_submit_blocked_by_block_error():
    adapter = _adapter(lambda *a: _report(100.0, 100.0, ok=False))
    with pytest.raises(PrintError) as ei:
        adapter.submit(_job(allow_paper_conversion=False))
    assert ei.value.code == PrintError.PAPER_SIZE_MISMATCH


# ---------------------------------------------------------------------------
# qt_print：resolve_effective_page 三态保持；转换注记含诚信提示
# ---------------------------------------------------------------------------
def _spec(paper, allow=False, tol=0.5):
    return LabelJobSpec(paper=paper, allow_paper_conversion=allow,
                        scale_tolerance_mm=tol, copies=1)


def test_qt_resolve_match_returns_paper():
    eff, note, err = qt_print.resolve_effective_page(
        50.0, 30.0, _spec(_paper(50, 30)))
    assert err is None and note is None
    assert eff == _paper(50, 30)


def test_qt_resolve_mismatch_block():
    eff, note, err = qt_print.resolve_effective_page(
        100.0, 100.0, _spec(_paper(50, 30), allow=False))
    assert eff is None and note is None
    assert err.code == PrintError.PAPER_SIZE_MISMATCH


def test_qt_resolve_converted_note_has_honesty():
    eff, note, err = qt_print.resolve_effective_page(
        100.0, 100.0, _spec(_paper(50, 30), allow=True))
    assert err is None
    assert eff.width_mm == 100.0
    assert "已按用户确认转换" in note
    assert "1:1" in note
    # 转换注记不得宣称软件控制驱动缩放
    assert "第三方驱动缩放不受本软件控制" in note
