"""ZPL 适配器测试：golden 字节、mm↔dots 边界、统一校验全覆盖。

内容模型走冻结接口 ``build_payload(job, *, content=None)``；不依赖 A 切片，
submit 在 Linux 仅断言 UNSUPPORTED_PLATFORM 分支，不用 mock 假装后端调用成功。
"""

import pytest

from client.printing.adapters._common import mm_to_dots
from client.printing.adapters.zpl import ZPLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelContent, LabelJobSpec


def make_caps(**over):
    base = dict(
        printer_name="Zebra-Test",
        transport=Transport.LOCAL,
        protocol=Protocol.ZPL,
        dpi=203,
        supported_papers=(PaperSize("100x60", 100.0, 60.0),),
        max_printable_width_mm=200.0,
        max_printable_height_mm=200.0,
    )
    base.update(over)
    return PrinterCapabilities(**base)


def make_content(**over):
    base = dict(
        items=[("123456789012", "CODE128")],
        template_copies=1,
        label_width_mm=100.0,
        label_height_mm=60.0,
        barcode_height_mm=15.0,
        show_text=True,
        module_mm=0.33,
        quiet_modules=11,
        label_gap_mm=2.0,
    )
    base.update(over)
    return LabelContent(**base)


def valid_job(**over):
    base = dict(
        paper=PaperSize("100x60", 100.0, 60.0),
        copies=1, dpi=203, module_mm=0.33,
        label_content=make_content(),
    )
    base.update(over)
    return LabelJobSpec(**base)


# ---------------------------------------------------------------------------
# golden：固定输入 → 精确字节串（100x60mm @203dpi，模块 0.33mm）
# ---------------------------------------------------------------------------

def test_golden_code128():
    adapter = ZPLAdapter(make_caps())
    payload = adapter.build_payload(valid_job())
    assert payload == (
        b"^XA\r\n"
        b"^PW799\r\n"
        b"^LL480\r\n"
        b"^LH0,0\r\n"
        b"^BY3,2,120\r\n"
        b"^FO33,24^BCN,120,Y,Y,N^FD123456789012^FS\r\n"
        b"^MMT\r\n"
        b"^PQ1\r\n"
        b"^XZ\r\n"
    )


def test_golden_ean13():
    adapter = ZPLAdapter(make_caps())
    job = valid_job(label_content=make_content(
        items=[("5901234123457", "EAN13")]))
    payload = adapter.build_payload(job)
    assert payload == (
        b"^XA\r\n"
        b"^PW799\r\n"
        b"^LL480\r\n"
        b"^LH0,0\r\n"
        b"^BY3,2,120\r\n"
        b"^FO33,24^B3,120,Y,N^FD5901234123457^FS\r\n"
        b"^MMT\r\n"
        b"^PQ1\r\n"
        b"^XZ\r\n"
    )


# ---------------------------------------------------------------------------
# 单位换算 mm ↔ dots 数学边界（唯一来源 units.mm_to_dots）
# ---------------------------------------------------------------------------

def test_mm_to_dots_basic_and_boundaries():
    assert mm_to_dots(0.0, 203) == 0
    # 1 inch = 25.4mm → 203dpi 恰好 203 dots
    assert mm_to_dots(25.4, 203) == 203
    assert mm_to_dots(-25.4, 203) == -203
    assert mm_to_dots(1.0, 300) == round(300 / 25.4)
    # 100mm @203dpi ≈ 799 dots（golden 用例的宽度）
    assert mm_to_dots(100.0, 203) == 799
    assert mm_to_dots(60.0, 203) == 480


def test_mm_to_dots_missing_dpi_raises():
    with pytest.raises(PrintError) as ei:
        mm_to_dots(10.0, 0)
    assert ei.value.code == PrintError.INVALID_JOB


# ---------------------------------------------------------------------------
# validate_job 全覆盖：error 阻止提交
# ---------------------------------------------------------------------------

def issue_codes(issues):
    return [(i.severity, i.code) for i in issues]


def test_valid_job_has_no_error():
    issues = ZPLAdapter(make_caps()).validate_job(valid_job())
    assert not any(s == "error" for s, _ in issue_codes(issues))


def test_missing_paper_is_error():
    issues = ZPLAdapter(make_caps()).validate_job(valid_job(paper=None))
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_nonpositive_paper_is_error():
    job = valid_job(paper=PaperSize("bad", 0.0, 60.0))
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_paper_not_in_supported_list_is_error():
    job = valid_job(paper=PaperSize("other", 60.0, 40.0))
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_UNSUPPORTED) in issue_codes(issues)


def test_paper_out_of_printable_area():
    job = valid_job(paper=PaperSize("big", 300.0, 100.0))
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.PAPER_OUT_OF_RANGE) in issue_codes(issues)


def test_quiet_zone_too_small():
    job = valid_job(quiet_zone_mm=1.0)  # 11×0.33=3.63mm 要求
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.QUIET_ZONE_TOO_SMALL) in issue_codes(issues)


@pytest.mark.parametrize("module_mm", [0.1, 1.0])
def test_module_width_out_of_range(module_mm):
    job = valid_job(module_mm=module_mm)
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.MODULE_WIDTH_OUT_OF_RANGE) in issue_codes(issues)


def test_missing_dpi_is_error():
    caps = make_caps(dpi=None, dpi_x=None, dpi_y=None)
    job = valid_job(dpi=None)
    issues = ZPLAdapter(caps).validate_job(job)
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


def test_invalid_copies_is_error():
    issues = ZPLAdapter(make_caps()).validate_job(valid_job(copies=0))
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


def test_offset_out_of_bounds_is_error():
    job = valid_job(offset_x_mm=120.0)  # 超出 100mm 标签宽
    issues = ZPLAdapter(make_caps()).validate_job(job)
    assert ("error", PrintError.INVALID_JOB) in issue_codes(issues)


# ---------------------------------------------------------------------------
# submit / cancel / query_job 语义（Linux 分支，不 mock 后端）
# ---------------------------------------------------------------------------

def test_submit_without_channel_on_linux_unsupported():
    adapter = ZPLAdapter(make_caps())
    with pytest.raises(PrintError) as ei:
        adapter.submit(valid_job())
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_cancel_not_supported():
    adapter = ZPLAdapter(make_caps())
    with pytest.raises(NotImplementedError):
        adapter.cancel(1)


def test_query_job_not_supported():
    adapter = ZPLAdapter(make_caps())
    with pytest.raises(NotImplementedError):
        adapter.query_job(1)


def test_build_payload_rejects_invalid_job():
    adapter = ZPLAdapter(make_caps(dpi=None, dpi_x=None, dpi_y=None))
    job = valid_job(dpi=None)
    with pytest.raises(PrintError) as ei:
        adapter.build_payload(job)
    assert ei.value.code == PrintError.INVALID_JOB
