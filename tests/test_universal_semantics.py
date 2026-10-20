"""UniversalWindowsAdapter.validate_job 分级语义测试（注入式 scale_checker）。

用构造的 ``ScaleReport`` 直测逻辑分支（非冒充硬件验收）：
- ok=True → 无尺寸/缩放 issue；
- ok=False 两轴均超容差 + allow_paper_conversion=False → error PAPER_SIZE_MISMATCH
  （has_error 阻止 submit）；
- ok=False 两轴均超容差 + allow=True → 降级 warning，注明已按用户确认转换；
- ok=False 单轴偏差 → 保持 SCALE_MISMATCH warning；
- checker 抛 FileNotFoundError（PDF 未落盘）→ 跳过，不阻断；
- submit 在 Linux 不触碰真实后端：error 时抛 PAPER_SIZE_MISMATCH，
  否则抛 UNSUPPORTED_PLATFORM。

运行方式（仓库根目录）：
    python3 -m pytest tests/test_universal_semantics.py -q
"""

import pytest

from client.printing.adapters._common import has_error
from client.printing.adapters.universal import UniversalWindowsAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec, ScaleReport


def make_caps():
    return PrinterCapabilities(
        printer_name="Queue-Test",
        transport=Transport.LOCAL,
        protocol=Protocol.UNIVERSAL,
        dpi=203,
        supported_papers=(PaperSize("50x30", 50.0, 30.0),),
        max_printable_width_mm=100.0,
        max_printable_height_mm=100.0,
    )


def _job(**over):
    base = dict(
        pdf_path="label.pdf",
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1,
        dpi=203,
        module_mm=0.33,
    )
    base.update(over)
    return LabelJobSpec(**base)


def _report(actual_w, actual_h, ok, warnings=None, tol=0.5):
    return ScaleReport(
        expected_width_mm=50.0,
        expected_height_mm=30.0,
        actual_width_mm=actual_w,
        actual_height_mm=actual_h,
        tolerance_mm=tol,
        ok=ok,
        warnings=warnings or [],
    )


# ---------------------------------------------------------------------------
# ok=True → 无尺寸/缩放 issue
# ---------------------------------------------------------------------------

def test_ok_report_produces_no_issue():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(50.0, 30.0, ok=True),
    )
    issues = adapter.validate_job(_job())
    scale_issues = [i for i in issues if i.code in (
        PrintError.SCALE_MISMATCH, PrintError.PAPER_SIZE_MISMATCH)]
    assert scale_issues == []
    assert not has_error(issues)


# ---------------------------------------------------------------------------
# 整体尺寸不一致：未许可 → error PAPER_SIZE_MISMATCH
# ---------------------------------------------------------------------------

def test_size_mismatch_without_conversion_is_error():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(100.0, 100.0, ok=False,
                                        warnings=["两轴均偏大"]),
    )
    issues = adapter.validate_job(_job(allow_paper_conversion=False))
    err = [i for i in issues if i.severity == "error"]
    assert len(err) == 1
    assert err[0].code == PrintError.PAPER_SIZE_MISMATCH
    assert has_error(issues)


def test_size_mismatch_with_conversion_is_warning_note():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(100.0, 100.0, ok=False,
                                        warnings=["两轴均偏大"]),
    )
    issues = adapter.validate_job(_job(allow_paper_conversion=True))
    size_issues = [i for i in issues if i.code == PrintError.PAPER_SIZE_MISMATCH]
    assert len(size_issues) == 1
    assert size_issues[0].severity == "warning"
    assert "已按用户确认转换" in size_issues[0].message
    assert not has_error(issues)


# ---------------------------------------------------------------------------
# 单轴/局部偏差 → 保持 SCALE_MISMATCH warning（不阻止）
# ---------------------------------------------------------------------------

def test_single_axis_deviation_kept_as_scale_warning():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(55.0, 30.0, ok=False,
                                        warnings=["宽度偏大 5mm"]),
    )
    issues = adapter.validate_job(_job())
    codes = [(i.severity, i.code) for i in issues]
    assert ("warning", PrintError.SCALE_MISMATCH) in codes
    assert not any(s == "error" for s, _ in codes)
    assert not any(c == PrintError.PAPER_SIZE_MISMATCH for _, c in codes)


# ---------------------------------------------------------------------------
# FileNotFoundError：PDF 未落盘 → 跳过检查，不阻断
# ---------------------------------------------------------------------------

def test_file_not_found_skips_check_without_issue():
    def _raise(*a):
        raise FileNotFoundError("label.pdf 尚未落盘")

    adapter = UniversalWindowsAdapter(make_caps(), scale_checker=_raise)
    issues = adapter.validate_job(_job())
    scale_issues = [i for i in issues if i.code in (
        PrintError.SCALE_MISMATCH, PrintError.PAPER_SIZE_MISMATCH)]
    assert scale_issues == []
    assert not has_error(issues)


# ---------------------------------------------------------------------------
# has_error 阻止 submit：error 直接抛出，不进入平台/后端
# ---------------------------------------------------------------------------

def test_submit_blocked_by_size_mismatch_error():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(100.0, 100.0, ok=False),
    )
    with pytest.raises(PrintError) as ei:
        adapter.submit(_job(allow_paper_conversion=False))
    assert ei.value.code == PrintError.PAPER_SIZE_MISMATCH


def test_submit_valid_job_on_linux_unsupported_platform():
    adapter = UniversalWindowsAdapter(
        make_caps(),
        scale_checker=lambda *a: _report(50.0, 30.0, ok=True),
    )
    with pytest.raises(PrintError) as ei:
        adapter.submit(_job())
    # Linux 无系统队列：不触碰真实后端
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_raw_spool_submit_on_linux_unsupported_platform():
    adapter = UniversalWindowsAdapter(make_caps(), scale_checker=None)
    job = LabelJobSpec(
        raw_payload=b"^XA^XZ",
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1, dpi=203, module_mm=0.33,
    )
    with pytest.raises(PrintError) as ei:
        adapter.submit(job)
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM
