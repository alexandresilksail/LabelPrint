"""冻结内容接口测试：LabelContent 驱动完整布局（ZPL/TSPL/CPCL）。

覆盖：单/多编码、template_copies 展开、job.copies 唯一写入协议打印数量、
偏移进入指令、不支持条码类型报错、DPI 不支持 error、静区不足 error、
缺 label_content → INVALID_JOB、显式 content 参数覆盖、show_text、GAP、模块宽。

不依赖 A 切片，不真实发包。
"""

import pytest

from client.printing.adapters.cpcl import CPCLAdapter
from client.printing.adapters.tspl import TSPLAdapter
from client.printing.adapters.zpl import ZPLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import LabelContent, LabelJobSpec


def make_caps(proto, dpi=203, **over):
    base = dict(
        printer_name="Content-Test",
        transport=Transport.LOCAL,
        protocol=proto,
        dpi=dpi,
        supported_papers=(PaperSize("L", 100.0, 60.0),),
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


def make_job(content=None, copies=1, dpi=203, **over):
    base = dict(
        paper=PaperSize("L", 100.0, 60.0),
        copies=copies, dpi=dpi, module_mm=0.33,
        label_content=content if content is not None else make_content(),
    )
    base.update(over)
    return LabelJobSpec(**base)


ADAPTERS = {
    Protocol.ZPL: ZPLAdapter,
    Protocol.TSPL: TSPLAdapter,
    Protocol.CPCL: CPCLAdapter,
}


# ---------------------------------------------------------------------------
# template_copies / 多编码展开：每个编码实例 = 一个指令块
# ---------------------------------------------------------------------------

def test_zpl_template_copies_expands_to_blocks():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    job = make_job(make_content(template_copies=2))
    payload = adapter.build_payload(job)
    assert payload.count(b"^XA") == 2
    assert payload.count(b"^XZ") == 2
    # 同一编码出现两次（每块一个 ^FD）
    assert payload.count(b"^FD123456789012^FS") == 2


def test_tspl_template_copies_expands_to_blocks():
    adapter = TSPLAdapter(make_caps(Protocol.TSPL))
    job = make_job(make_content(template_copies=2))
    payload = adapter.build_payload(job)
    assert payload.count(b"CLS") == 2
    assert payload.count(b"EOP") == 2
    assert payload.count(b'"123456789012"') == 2


def test_cpcl_template_copies_expands_to_blocks():
    adapter = CPCLAdapter(make_caps(Protocol.CPCL))
    job = make_job(make_content(template_copies=2))
    payload = adapter.build_payload(job)
    assert payload.count(b"FORM") == 2
    assert payload.count(b"123456789012") == 2


def test_multiple_items_each_becomes_its_own_block():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    content = make_content(items=[("AAA111", "CODE128"), ("5901234123457", "EAN13")])
    payload = adapter.build_payload(make_job(content))
    assert payload.count(b"^XA") == 2
    assert b"^FDAAA111^FS" in payload
    assert b"^FD5901234123457^FS" in payload


# ---------------------------------------------------------------------------
# job.copies 唯一写入协议打印数量（不与其它处重复）
# ---------------------------------------------------------------------------

def test_zpl_copies_written_as_pq():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    payload = adapter.build_payload(make_job(copies=3))
    assert payload.count(b"^PQ3") == 1
    assert b"^PQ1" not in payload


def test_tspl_copies_written_as_print_n():
    adapter = TSPLAdapter(make_caps(Protocol.TSPL))
    payload = adapter.build_payload(make_job(copies=3))
    assert payload.count(b"PRINT 3") == 1
    assert b"PRINT 1" not in payload


def test_cpcl_copies_written_as_print_n():
    adapter = CPCLAdapter(make_caps(Protocol.CPCL))
    payload = adapter.build_payload(make_job(copies=3))
    assert payload.count(b"PRINT 3") == 1
    assert b"PRINT 1" not in payload


def test_copies_not_multiplied_by_template_copies():
    # template_copies=2 × copies=3：块数=2，每块打印数=3，不得变成 6
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    content = make_content(template_copies=2)
    payload = adapter.build_payload(make_job(content, copies=3))
    assert payload.count(b"^XA") == 2
    assert payload.count(b"^PQ3") == 2


# ---------------------------------------------------------------------------
# 偏移实际进入指令
# ---------------------------------------------------------------------------

def test_offset_enters_zpl_lh():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    content = make_content(offset_x_mm=2.54, offset_y_mm=5.0)  # 20 / 40 dots
    payload = adapter.build_payload(make_job(content))
    assert b"^LH20,40" in payload


def test_offset_enters_tspl_reference():
    adapter = TSPLAdapter(make_caps(Protocol.TSPL))
    content = make_content(offset_x_mm=2.54, offset_y_mm=5.0)
    payload = adapter.build_payload(make_job(content))
    assert b"REFERENCE 20,40" in payload


def test_offset_shifts_cpcl_absolute_coordinate():
    adapter = CPCLAdapter(make_caps(Protocol.CPCL))
    content = make_content(offset_x_mm=2.54)  # 33 + 20 = 53
    payload = adapter.build_payload(make_job(content))
    assert b"BARCODE CODE128 53 24 120" in payload


# ---------------------------------------------------------------------------
# 间隙 / 模块宽度 / 人眼可读
# ---------------------------------------------------------------------------

def test_gap_enters_tspl_gap_command():
    adapter = TSPLAdapter(make_caps(Protocol.TSPL))
    content = make_content(label_gap_mm=2.0)  # ≈16 dots @203dpi
    payload = adapter.build_payload(make_job(content))
    assert b"GAP 16 dots, 0" in payload


def test_module_width_enters_zpl_by():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    content = make_content(module_mm=0.5)  # round(0.5*203/25.4)=4 dots
    payload = adapter.build_payload(make_job(content))
    assert b"^BY4,2,120" in payload


def test_show_text_false_zpl_hides_human_readable():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    content = make_content(show_text=False)
    payload = adapter.build_payload(make_job(content))
    assert b"^BCN,120,N,Y,N" in payload


def test_show_text_false_tspl_hides_human_readable():
    adapter = TSPLAdapter(make_caps(Protocol.TSPL))
    content = make_content(show_text=False)
    payload = adapter.build_payload(make_job(content))
    assert b'CODE128 33,24,"123456789012",120,0,0,2,2,48,0' in payload


# ---------------------------------------------------------------------------
# 不支持的条码类型 → PrintError（禁止静默退化）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proto", [Protocol.ZPL, Protocol.TSPL, Protocol.CPCL])
def test_unsupported_barcode_type_raises(proto):
    adapter = ADAPTERS[proto](make_caps(proto))
    content = make_content(items=[("abc", "QRCODE")])
    with pytest.raises(PrintError) as ei:
        adapter.build_payload(make_job(content))
    assert ei.value.code == PrintError.INVALID_JOB
    assert "不支持的条码类型" in str(ei.value)
    assert "QRCODE" in str(ei.value)
    # resolution 列出支持类型
    assert "CODE128" in ei.value.resolution


# ---------------------------------------------------------------------------
# DPI：设备分辨率不在支持列表且无显式 dpi → error（禁止静默用错 DPI）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proto", [Protocol.ZPL, Protocol.TSPL, Protocol.CPCL])
def test_unsupported_device_dpi_is_error_issue(proto):
    caps = make_caps(proto, dpi=305)  # 305 不在 (203,300,600)
    adapter = ADAPTERS[proto](caps)
    job = make_job(dpi=None)  # 无显式 dpi，禁止静默采用未知分辨率
    issues = adapter.validate_job(job)
    codes = [(i.severity, i.code) for i in issues]
    assert ("error", PrintError.INVALID_JOB) in codes
    with pytest.raises(PrintError) as ei:
        adapter.build_payload(job)
    assert ei.value.code == PrintError.INVALID_JOB


@pytest.mark.parametrize("proto", [Protocol.ZPL, Protocol.TSPL, Protocol.CPCL])
def test_explicit_dpi_is_respected(proto):
    caps = make_caps(proto, dpi=305)
    adapter = ADAPTERS[proto](caps)
    job = make_job(dpi=305)  # 用户显式指定，尊重显式选择，不拦截
    issues = adapter.validate_job(job)
    assert not any(i.severity == "error" for i in issues)
    # 显式 DPI 可正常构建（TSPL 经 DENSITY 进入指令）
    payload = adapter.build_payload(job)
    assert payload


# ---------------------------------------------------------------------------
# 静区几何不足 → error issue
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proto", [Protocol.ZPL, Protocol.TSPL, Protocol.CPCL])
def test_quiet_zone_geometry_too_small(proto):
    caps = make_caps(proto)
    adapter = ADAPTERS[proto](caps)
    # 30mm 标签放不下 12 位 CODE128（含两侧静区）
    content = make_content(label_width_mm=30.0, label_height_mm=20.0)
    job = LabelJobSpec(
        paper=PaperSize("S", 30.0, 20.0), copies=1, dpi=203,
        module_mm=0.33, label_content=content,
    )
    # 让 supported_papers 命中 30x20
    caps.supported_papers = (PaperSize("S", 30.0, 20.0),)
    issues = adapter.validate_job(job)
    codes = [(i.severity, i.code) for i in issues]
    assert ("error", PrintError.QUIET_ZONE_TOO_SMALL) in codes
    with pytest.raises(PrintError) as ei:
        adapter.build_payload(job)
    assert ei.value.code == PrintError.QUIET_ZONE_TOO_SMALL


# ---------------------------------------------------------------------------
# 缺 label_content → INVALID_JOB；显式 content 参数覆盖 job.label_content
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proto", [Protocol.ZPL, Protocol.TSPL, Protocol.CPCL])
def test_missing_label_content_invalid_job(proto):
    adapter = ADAPTERS[proto](make_caps(proto))
    job = LabelJobSpec(
        paper=PaperSize("L", 100.0, 60.0), copies=1, dpi=203)
    with pytest.raises(PrintError) as ei:
        adapter.build_payload(job)
    assert ei.value.code == PrintError.INVALID_JOB
    assert "缺少 label_content" in str(ei.value)


def test_explicit_content_param_overrides_job_content():
    adapter = ZPLAdapter(make_caps(Protocol.ZPL))
    job = make_job(make_content(items=[("FROM_JOB", "CODE128")]))
    override = make_content(items=[("FROM_PARAM", "CODE128")])
    payload = adapter.build_payload(job, content=override)
    assert b"^FDFROM_PARAM^FS" in payload
    assert b"FROM_JOB" not in payload
