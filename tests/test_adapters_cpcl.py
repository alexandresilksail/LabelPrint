"""CPCL 适配器测试：golden 字节、mm↔dots 边界、统一校验全覆盖。

不依赖 A 切片；submit 在 Linux 仅断言 UNSUPPORTED_PLATFORM 分支。
"""

import pytest

from client.printing.adapters._common import mm_to_dots
from client.printing.adapters.cpcl import CPCLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec


def make_caps(**over):
    base = dict(
        printer_name="Honeywell-Test",
        transport=Transport.LOCAL,
        protocol=Protocol.CPCL,
        dpi=203,
        supported_papers=(PaperSize("50x30", 50.0, 30.0),),
        max_printable_width_mm=100.0,
        max_printable_height_mm=100.0,
    )
    base.update(over)
    return PrinterCapabilities(**base)


def valid_job(**over):
    base = dict(
        paper=PaperSize("50x30", 50.0, 30.0),
        copies=1, dpi=203, module_mm=0.33,
    )
    base.update(over)
    return LabelJobSpec(**base)


# ---------------------------------------------------------------------------
# golden
# ---------------------------------------------------------------------------

def test_golden_code128():
    adapter = CPCLAdapter(make_caps())
    payload = adapter.build_payload(
        valid_job(), barcode_data="123456789012", barcode_type="CODE128")
    assert payload == (
        b"! 0 203 203 240 1\r\n"
        b"PAGE-WIDTH 400\r\n"
        b"BARCODE CODE128 24 24 120 123456789012\r\n"
        b"FORM\r\n"
        b"PRINT 1\r\n"
    )


def test_golden_ean13():
    adapter = CPCLAdapter(make_caps())
    payload = adapter.build_payload(
        valid_job(), barcode_data="5901234123457", barcode_type="EAN13")
    assert payload == (
        b"! 0 203 203 240 1\r\n"
        b"PAGE-WIDTH 400\r\n"
        b"BARCODE EAN13 24 24 120 5901234123457\r\n"
        b"FORM\r\n"
        b"PRINT 1\r\n"
    )


def test_offset_shifts_origin_in_cpcl():
    adapter = CPCLAdapter(make_caps())
    job = valid_job(offset_x_mm=2.54)  # ≈20 dots @203dpi
    payload = adapter.build_payload(job, barcode_data="123456789012")
    # fx = 24 + 20 = 44
    assert b"BARCODE CODE128 44 24 120" in payload


# ---------------------------------------------------------------------------
# 换算边界
# ---------------------------------------------------------------------------

def test_mm_to_dots_boundaries():
    assert mm_to_dots(25.4, 203) == 203
    assert mm_to_dots(50.0, 203) == 400
    assert mm_to_dots(30.0, 203) == 240


# ---------------------------------------------------------------------------
# 校验全覆盖
# ---------------------------------------------------------------------------

def issue_codes(issues):
    return [(i.severity, i.code) for i in issues]


def test_valid_job_has_no_error():
    issues = CPCLAdapter(make_caps()).validate_job(valid_job())
    assert not any(s == "error" for s, _ in issue_codes(issues))


def test_missing_paper_is_error():
    issues = CPCLAdapter(make_caps()).validate_job(valid_job(paper=None))
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_paper_not_supported_is_error():
    job = valid_job(paper=PaperSize("other", 60.0, 40.0))
    issues = CPCLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_paper_out_of_printable_area():
    job = valid_job(paper=PaperSize("big", 200.0, 100.0))
    issues = CPCLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_OUT_OF_RANGE) in issue_codes(issues)


def test_quiet_zone_too_small():
    issues = CPCLAdapter(make_caps()).validate_job(valid_job(quiet_zone_mm=1.0))
    assert ("error", PrintError.QUIET_ZONE_TOO_SMALL) in issue_codes(issues)


@pytest.mark.parametrize("module_mm", [0.1, 1.0])
def test_module_width_out_of_range(module_mm):
    issues = CPCLAdapter(make_caps()).validate_job(valid_job(module_mm=module_mm))
    assert ("error", PrintError.MODULE_WIDTH_OUT_OF_RANGE) in issue_codes(issues)


def test_missing_dpi_is_error():
    caps = make_caps(dpi=None, dpi_x=None, dpi_y=None)
    issues = CPCLAdapter(caps).validate_job(valid_job(dpi=None))
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


def test_invalid_copies_is_error():
    issues = CPCLAdapter(make_caps()).validate_job(valid_job(copies=0))
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


# ---------------------------------------------------------------------------
# submit / cancel / query_job（Linux 分支）
# ---------------------------------------------------------------------------

def test_submit_without_channel_on_linux_unsupported():
    with pytest.raises(PrintError) as ei:
        CPCLAdapter(make_caps()).submit(valid_job())
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_cancel_not_supported():
    with pytest.raises(NotImplementedError):
        CPCLAdapter(make_caps()).cancel(1)


def test_query_job_not_supported():
    with pytest.raises(NotImplementedError):
        CPCLAdapter(make_caps()).query_job(1)
