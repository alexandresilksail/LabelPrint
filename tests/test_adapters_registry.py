"""注册表分发、未实测标识透传，以及 Universal 后端选择纯函数测试。

不依赖 A 切片：Universal 的后端选择逻辑用纯函数与注入式 scale_checker
测试；submit 在 Linux 仅断言 UNSUPPORTED_PLATFORM，不用 mock 假装成功。
"""

import pytest

from client.printing.adapters.cpcl import CPCLAdapter
from client.printing.adapters.registry import (
    build_adapter, protocol_supported, verified,
)
from client.printing.adapters.tspl import TSPLAdapter
from client.printing.adapters.universal import (
    UniversalWindowsAdapter, _choose_backend,
)
from client.printing.adapters.zpl import ZPLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec, ScaleReport


def make_caps(protocol=Protocol.UNIVERSAL, **over):
    base = dict(
        printer_name="Queue-Test",
        transport=Transport.LOCAL,
        protocol=protocol,
        dpi=203,
        supported_papers=(PaperSize("50x30", 50.0, 30.0),),
        max_printable_width_mm=100.0,
        max_printable_height_mm=100.0,
    )
    base.update(over)
    return PrinterCapabilities(**base)


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("protocol,expected", [
    (Protocol.UNIVERSAL, UniversalWindowsAdapter),
    (Protocol.ZPL, ZPLAdapter),
    (Protocol.TSPL, TSPLAdapter),
    (Protocol.CPCL, CPCLAdapter),
])
def test_build_adapter_dispatch(protocol, expected):
    adapter = build_adapter(make_caps(protocol=protocol))
    assert isinstance(adapter, expected)


def test_unknown_protocol_unsupported():
    caps = make_caps()
    caps.protocol = "snmp"  # 未登记协议
    with pytest.raises(PrintError) as ei:
        build_adapter(caps)
    assert ei.value.code == PrintError.PROTOCOL_UNSUPPORTED


@pytest.mark.parametrize("protocol,expected", [
    ("universal", True), ("zpl", True), ("tspl", True), ("cpcl", True),
    ("escpos", False), ("snmp", False),
])
def test_protocol_supported(protocol, expected):
    assert protocol_supported(protocol) is expected


# ---------------------------------------------------------------------------
# 未实测标识透传（严禁伪造）
# ---------------------------------------------------------------------------

def test_verified_passthrough_false_by_default():
    adapter = build_adapter(make_caps(verified_with_hardware=False))
    assert verified(adapter) is False


def test_verified_passthrough_true_when_hardware_tested():
    adapter = build_adapter(make_caps(verified_with_hardware=True))
    assert verified(adapter) is True


# ---------------------------------------------------------------------------
# Universal：后端选择纯函数
# ---------------------------------------------------------------------------

def test_choose_backend_pdf_qt():
    job = LabelJobSpec(pdf_path="label.pdf")
    assert _choose_backend(job) == "pdf_qt"


def test_choose_backend_raw_spool():
    job = LabelJobSpec(raw_payload=b"^XA^XZ")
    assert _choose_backend(job) == "raw_spool"


def test_choose_backend_empty_raises():
    with pytest.raises(PrintError) as ei:
        _choose_backend(LabelJobSpec())
    assert ei.value.code == PrintError.INVALID_JOB


# ---------------------------------------------------------------------------
# Universal：统一校验 + 注入式缩放检查（warning，不阻止）
# ---------------------------------------------------------------------------

def _mismatch_report(path, paper, tolerance_mm):
    return ScaleReport(
        expected_width_mm=paper.width_mm,
        expected_height_mm=paper.height_mm,
        actual_width_mm=paper.width_mm + 5.0,
        actual_height_mm=paper.height_mm,
        tolerance_mm=tolerance_mm,
        ok=False,
        warnings=["宽度偏大 5mm"],
    )


def test_universal_scale_mismatch_is_warning_not_error():
    adapter = UniversalWindowsAdapter(
        make_caps(), scale_checker=_mismatch_report)
    job = LabelJobSpec(
        pdf_path="label.pdf",
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1, dpi=203, module_mm=0.33)
    issues = adapter.validate_job(job)
    codes = [(i.severity, i.code) for i in issues]
    assert ("warning", PrintError.SCALE_MISMATCH) in codes
    assert not any(s == "error" for s, _ in codes)


def test_universal_valid_job_no_error():
    adapter = UniversalWindowsAdapter(make_caps(), scale_checker=lambda *a: ScaleReport(
        expected_width_mm=50, expected_height_mm=30,
        actual_width_mm=50, actual_height_mm=30, ok=True))
    job = LabelJobSpec(
        pdf_path="label.pdf",
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1, dpi=203, module_mm=0.33)
    issues = adapter.validate_job(job)
    assert not any(i.severity == "error" for i in issues)


def test_universal_submit_on_linux_unsupported():
    # 注入 scale_checker：不依赖 A 切片 pdf_render 真实落盘文件
    adapter = UniversalWindowsAdapter(
        make_caps(), scale_checker=_ok_report)
    job = LabelJobSpec(
        pdf_path="label.pdf",
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1, dpi=203, module_mm=0.33)
    with pytest.raises(PrintError) as ei:
        adapter.submit(job)
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def _ok_report(path, paper, tolerance_mm):
    return ScaleReport(
        expected_width_mm=paper.width_mm,
        expected_height_mm=paper.height_mm,
        actual_width_mm=paper.width_mm,
        actual_height_mm=paper.height_mm,
        tolerance_mm=tolerance_mm,
        ok=True,
    )
