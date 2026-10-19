"""TSPL 适配器测试：golden 字节、mm↔dots 边界、统一校验全覆盖。

不依赖 A 切片；submit 在 Linux 仅断言 UNSUPPORTED_PLATFORM 分支。
"""

import pytest

from client.printing.adapters._common import mm_to_dots
from client.printing.adapters.tspl import TSPLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelJobSpec


def make_caps(**over):
    base = dict(
        printer_name="TSC-Test",
        transport=Transport.LOCAL,
        protocol=Protocol.TSPL,
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
        copies=1, dpi=203, module_mm=0.33, label_gap_mm=2.0,
    )
    base.update(over)
    return LabelJobSpec(**base)


# ---------------------------------------------------------------------------
# golden
# ---------------------------------------------------------------------------

def test_golden_code128():
    adapter = TSPLAdapter(make_caps())
    payload = adapter.build_payload(
        valid_job(), barcode_data="123456789012", barcode_type="CODE128")
    assert payload == (
        b"SIZE 400 dots, 240 dots\r\n"
        b"GAP 16 dots, 0\r\n"
        b"DENSITY 203\r\n"
        b"DIRECTION 1,0\r\n"
        b"REFERENCE 0,0\r\n"
        b"CLS\r\n"
        b'CODE128 24,24,"123456789012",120,1,0,2,2,48,0\r\n'
        b"PRINT 1\r\n"
        b"EOP\r\n"
    )


def test_golden_ean13():
    adapter = TSPLAdapter(make_caps())
    payload = adapter.build_payload(
        valid_job(), barcode_data="5901234123457", barcode_type="EAN13")
    assert payload == (
        b"SIZE 400 dots, 240 dots\r\n"
        b"GAP 16 dots, 0\r\n"
        b"DENSITY 203\r\n"
        b"DIRECTION 1,0\r\n"
        b"REFERENCE 0,0\r\n"
        b"CLS\r\n"
        b'EAN13 24,24,"5901234123457",120,1,0,2,2,0\r\n'
        b"PRINT 1\r\n"
        b"EOP\r\n"
    )


# ---------------------------------------------------------------------------
# 换算边界
# ---------------------------------------------------------------------------

def test_mm_to_dots_boundaries():
    assert mm_to_dots(25.4, 203) == 203
    assert mm_to_dots(-25.4, 300) == -300
    assert mm_to_dots(50.0, 203) == 400
    # 2mm 间隙 @203dpi ≈ 16 dots（golden GAP）
    assert mm_to_dots(2.0, 203) == 16


# ---------------------------------------------------------------------------
# 校验全覆盖
# ---------------------------------------------------------------------------

def issue_codes(issues):
    return [(i.severity, i.code) for i in issues]


def test_valid_job_has_no_error():
    issues = TSPLAdapter(make_caps()).validate_job(valid_job())
    assert not any(s == "error" for s, _ in issue_codes(issues))


def test_missing_paper_is_error():
    issues = TSPLAdapter(make_caps()).validate_job(valid_job(paper=None))
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_paper_out_of_printable_area():
    job = valid_job(paper=PaperSize("big", 200.0, 100.0))
    issues = TSPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_OUT_OF_RANGE) in issue_codes(issues)


def test_quiet_zone_too_small():
    job = valid_job(quiet_zone_mm=1.0)
    issues = TSPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.QUIET_ZONE_TOO_SMALL) in issue_codes(issues)


@pytest.mark.parametrize("module_mm", [0.1, 1.0])
def test_module_width_out_of_range(module_mm):
    issues = TSPLAdapter(make_caps()).validate_job(valid_job(module_mm=module_mm))
    assert ("error", PrintError.MODULE_WIDTH_OUT_OF_RANGE) in issue_codes(issues)


def test_missing_dpi_is_error():
    caps = make_caps(dpi=None, dpi_x=None, dpi_y=None)
    issues = TSPLAdapter(caps).validate_job(valid_job(dpi=None))
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


def test_invalid_copies_is_error():
    issues = TSPLAdapter(make_caps()).validate_job(valid_job(copies=0))
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


# ---------------------------------------------------------------------------
# submit / cancel / query_job（Linux 分支）
# ---------------------------------------------------------------------------

def test_submit_without_channel_on_linux_unsupported():
    with pytest.raises(PrintError) as ei:
        TSPLAdapter(make_caps()).submit(valid_job())
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_cancel_not_supported():
    with pytest.raises(NotImplementedError):
        TSPLAdapter(make_caps()).cancel(1)


def test_query_job_not_supported():
    with pytest.raises(NotImplementedError):
        TSPLAdapter(make_caps()).query_job(1)
