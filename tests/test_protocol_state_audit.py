"""第二轮审计 P1「打印协议和设备能力」强化测试。

诚信红线（与审计点一一对应）：
1. 状态严格区分：Windows 队列提交 = QUEUED；TCP 9100 发送成功 = SENT
   （无设备确认回执）；任何当前通道都不得产生 PHYSICALLY_PRINTED；
   失败 = FAILED 且携带登记错误码。
2. 未集成通道：USB / 蓝牙 open/write 一律 TRANSPORT_NOT_IMPLEMENTED；
   LPR 经 build_channel 抛 TRANSPORT_NOT_IMPLEMENTED；非 Windows 对
   USB/蓝牙抛 UNSUPPORTED_PLATFORM；禁止伪装可用。
3. verified_with_hardware 守卫：默认 False；from_dict 缺省/None/False → False；
   全仓库 client/ 源码不得出现把 verified_with_hardware 赋为 True 的字面量。
4. 协议分发：build_adapter 只按显式 protocol；未知协议 PROTOCOL_UNSUPPORTED；
   protocol_selection_basis 不按厂商名/驱动名推断。
5. submit 路径：按 channel.kind 判定 SENT/QUEUED；OSError 映射 SPOOL_FAILED；
   PrintError 原样上抛，不吞异常、不把失败改写成成功。

不真实发包：网络只注入 fake channel；不触碰系统队列与真实设备。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from client.printing.adapters import transports as tx
from client.printing.adapters.cpcl import CPCLAdapter
from client.printing.adapters.registry import (
    build_adapter, protocol_selection_basis, verified,
)
from client.printing.adapters.transports import (
    BluetoothChannel, UsbChannel, build_channel,
)
from client.printing.adapters.tspl import TSPLAdapter
from client.printing.adapters.universal import UniversalWindowsAdapter
from client.printing.adapters.zpl import ZPLAdapter
from client.printing.capabilities import (
    DriverInfo, PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import (
    JobOutcome, LabelContent, LabelJobSpec, PrintResult, ScaleReport,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CLIENT_DIR = _REPO_ROOT / "client"

ADAPTER_CLASSES = {
    Protocol.ZPL: ZPLAdapter,
    Protocol.TSPL: TSPLAdapter,
    Protocol.CPCL: CPCLAdapter,
}


# ---------------------------------------------------------------------------
# 公共夹具（注入式，不碰硬件）
# ---------------------------------------------------------------------------

def _make_caps(proto: Protocol, transport: Transport = Transport.NETWORK) \
        -> PrinterCapabilities:
    return PrinterCapabilities(
        printer_name="Audit-Printer",
        transport=transport,
        protocol=proto,
        dpi=203,
        supported_papers=(PaperSize("L", 100.0, 60.0),),
        max_printable_width_mm=200.0,
        max_printable_height_mm=200.0,
    )


def _make_job() -> LabelJobSpec:
    content = LabelContent(
        items=[("123456789012", "CODE128")],
        label_width_mm=100.0,
        label_height_mm=60.0,
        barcode_height_mm=15.0,
        module_mm=0.33,
        quiet_modules=11,
    )
    return LabelJobSpec(
        paper=PaperSize("L", 100.0, 60.0),
        copies=1,
        dpi=203,
        module_mm=0.33,
        label_content=content,
    )


class _FakeChannel:
    """注入式 fake 通道：只记录写入/按回调抛错，不建立任何连接。"""

    def __init__(self, kind: str, on_open=None, on_write=None):
        self.kind = kind
        self._on_open = on_open
        self._on_write = on_write
        self.written: bytes | None = None
        self.closed = False

    def open(self):
        if self._on_open is not None:
            self._on_open()

    def write(self, data: bytes):
        if self._on_write is not None:
            self._on_write()
        self.written = data

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def is_open(self):
        return True


def _raise(exc: Exception) -> None:
    raise exc


# ---------------------------------------------------------------------------
# 审计点 1：四种状态严格区分（PrintResult 工厂层）
# ---------------------------------------------------------------------------

def test_outcome_values_are_distinct():
    values = [o.value for o in JobOutcome]
    assert len(values) == len(set(values)), "四种状态值不得互相混淆"


def test_queued_factory_exact_semantics():
    r = PrintResult.queued("WinQueue", 7)
    assert r.outcome is JobOutcome.QUEUED
    assert r.outcome is not JobOutcome.SENT
    assert r.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert r.succeeded is True
    assert "已提交到打印队列" in r.message
    assert "不代表物理打印完成" in r.message
    assert "物理打印成功" not in r.message


def test_sent_factory_exact_semantics():
    r = PrintResult.sent("Net-9100")
    assert r.outcome is JobOutcome.SENT
    assert r.outcome is not JobOutcome.QUEUED
    assert r.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert r.succeeded is True
    assert "无确认回执" in r.message
    assert "不代表物理打印完成" in r.message
    assert "物理打印成功" not in r.message


def test_failed_factory_keeps_error_code():
    err = PrintError(PrintError.SPOOL_FAILED, "写入失败")
    r = PrintResult.failed("P", err)
    assert r.outcome is JobOutcome.FAILED
    assert r.succeeded is False
    assert r.error is err
    assert r.error.code == PrintError.SPOOL_FAILED


def test_no_factory_produces_physically_printed():
    # 工厂层只允许 QUEUED/SENT/FAILED/CANCELLED；PHYSICALLY_PRINTED 无产出路径
    assert PrintResult.queued("Q", None).outcome is not \
        JobOutcome.PHYSICALLY_PRINTED
    assert PrintResult.sent("N").outcome is not \
        JobOutcome.PHYSICALLY_PRINTED
    assert PrintResult.cancelled("Q", None).outcome is not \
        JobOutcome.PHYSICALLY_PRINTED


# ---------------------------------------------------------------------------
# 审计点 1 + 5：submit 按 channel.kind 精确判定 SENT/QUEUED；失败不吞
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("proto", list(ADAPTER_CLASSES))
def test_tcp_submit_exactly_sent(proto):
    channel = _FakeChannel("tcp")
    adapter = ADAPTER_CLASSES[proto](_make_caps(proto), channel=channel)
    result = adapter.submit(_make_job())
    assert result.outcome is JobOutcome.SENT
    assert result.outcome is not JobOutcome.QUEUED
    assert result.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert result.succeeded is True
    assert "无确认回执" in result.message or "不代表物理打印完成" in result.message
    assert "物理打印成功" not in result.message
    assert channel.written, "指令必须已写入通道"


@pytest.mark.parametrize("proto", list(ADAPTER_CLASSES))
def test_spool_submit_exactly_queued(proto):
    channel = _FakeChannel("spool")
    adapter = ADAPTER_CLASSES[proto](_make_caps(proto), channel=channel)
    result = adapter.submit(_make_job())
    assert result.outcome is JobOutcome.QUEUED
    assert result.outcome is not JobOutcome.SENT
    assert result.outcome is not JobOutcome.PHYSICALLY_PRINTED
    assert result.succeeded is True
    assert "不代表物理打印完成" in result.message
    assert "物理打印成功" not in result.message


@pytest.mark.parametrize("proto", list(ADAPTER_CLASSES))
def test_submit_oserror_maps_to_spool_failed(proto):
    channel = _FakeChannel(
        "tcp", on_open=lambda: _raise(OSError("connection refused")))
    adapter = ADAPTER_CLASSES[proto](_make_caps(proto), channel=channel)
    with pytest.raises(PrintError) as ei:
        adapter.submit(_make_job())
    assert ei.value.code == PrintError.SPOOL_FAILED
    # 失败不得伪装成成功结果
    assert not hasattr(ei.value, "outcome") or getattr(
        ei.value, "outcome", None) is not JobOutcome.SENT


@pytest.mark.parametrize("proto", list(ADAPTER_CLASSES))
def test_submit_print_error_not_swallowed(proto):
    """通道抛出的 PrintError（如未集成）必须原样上抛，不得吞掉或改写。"""
    channel = _FakeChannel(
        "tcp",
        on_open=lambda: _raise(
            PrintError(PrintError.TRANSPORT_NOT_IMPLEMENTED, "尚未集成")),
    )
    adapter = ADAPTER_CLASSES[proto](_make_caps(proto), channel=channel)
    with pytest.raises(PrintError) as ei:
        adapter.submit(_make_job())
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_universal_submit_on_linux_does_not_fake_success():
    """通用队列路径在非 Windows 必须抛 UNSUPPORTED_PLATFORM，不得假装成功。"""
    ok_report = ScaleReport(
        expected_width_mm=100.0, expected_height_mm=60.0,
        actual_width_mm=100.0, actual_height_mm=60.0, ok=True)
    adapter = UniversalWindowsAdapter(
        _make_caps(Protocol.UNIVERSAL, transport=Transport.LOCAL),
        scale_checker=lambda *a: ok_report,
    )
    job = LabelJobSpec(
        pdf_path="label.pdf",
        paper=PaperSize("L", 100.0, 60.0),
        copies=1, dpi=203, module_mm=0.33,
    )
    with pytest.raises(PrintError) as ei:
        adapter.submit(job)
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# 审计点 2：USB / 蓝牙 / LPR 未集成，禁止伪装可用
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channel", [
    UsbChannel(r"\\?\usb#vid_1234&pid_5678"),
    BluetoothChannel("COM5"),
])
@pytest.mark.parametrize("method", ["open", "write"])
def test_usb_bluetooth_open_write_not_implemented(channel, method):
    with pytest.raises(PrintError) as ei:
        if method == "open":
            channel.open()
        else:
            channel.write(b"^XA^XZ")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


@pytest.mark.parametrize("port_name", [
    "lpr://192.168.1.20", "lpr:192.168.1.20:515",
])
def test_build_channel_lpr_not_implemented(port_name, monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: False)
    caps = PrinterCapabilities(
        printer_name="P", transport=Transport.NETWORK, port_name=port_name)
    with pytest.raises(PrintError) as ei:
        build_channel(caps)
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


@pytest.mark.parametrize("transport", [Transport.USB, Transport.BLUETOOTH])
def test_build_usb_bt_unsupported_on_non_windows(transport, monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: False)
    caps = PrinterCapabilities(
        printer_name="P", transport=transport, port_name="X")
    with pytest.raises(PrintError) as ei:
        build_channel(caps)
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


@pytest.mark.parametrize("transport", [Transport.USB, Transport.BLUETOOTH])
def test_build_usb_bt_not_implemented_even_on_windows(transport, monkeypatch):
    """即便在 Windows 上，未集成的 raw 通道也只能如实报错。"""
    monkeypatch.setattr(tx, "is_windows", lambda: True)
    caps = PrinterCapabilities(
        printer_name="P", transport=transport, port_name="X")
    with pytest.raises(PrintError) as ei:
        build_channel(caps)
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


# ---------------------------------------------------------------------------
# 审计点 3：verified_with_hardware 守卫（默认 False，源码无 True 赋值）
# ---------------------------------------------------------------------------

def test_default_capabilities_verified_false():
    assert PrinterCapabilities(printer_name="P").verified_with_hardware is False


@pytest.mark.parametrize("payload,expected", [
    ({}, False),
    ({"verified_with_hardware": False}, False),
    ({"verified_with_hardware": None}, False),
    ({"verified_with_hardware": True}, True),
    # 未知/非布尔值一律不得当 True（禁止 bool("false") 这类误标）
    ({"verified_with_hardware": "false"}, False),
    ({"verified_with_hardware": "0"}, False),
    ({"verified_with_hardware": "no"}, False),
    ({"verified_with_hardware": ""}, False),
    ({"verified_with_hardware": "yes"}, True),
    ({"verified_with_hardware": 1}, True),
])
def test_from_dict_verified_guard(payload, expected):
    caps = PrinterCapabilities.from_dict({"printer_name": "P", **payload})
    assert caps.verified_with_hardware is expected


def test_registry_verified_passthrough_default_false():
    adapter = build_adapter(_make_caps(Protocol.UNIVERSAL))
    assert verified(adapter) is False


def test_source_has_no_verified_true_assignment():
    """全仓库 client/ 源码不得出现 verified_with_hardware = True 字面量。

    未实测型号禁止标已验证；唯一允许 True 的路径是存在真实验收记录后
    显式构造，源码内不得预置任何 True 赋值。
    """
    pat = re.compile(r"verified_with_hardware\s*(?<![=<>!])=(?![=])\s*True\b")
    offenders: list[str] = []
    for py in _CLIENT_DIR.rglob("*.py"):
        for lineno, line in enumerate(
                py.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pat.search(line):
                offenders.append(f"{py.relative_to(_REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "源码不得把 verified_with_hardware 赋为 True：" + "; ".join(offenders))


# ---------------------------------------------------------------------------
# 审计点 4：协议分发只按显式 protocol，无品牌推断
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_protocol", ["snmp", "escpos", 123, None])
def test_unknown_protocol_unsupported(bad_protocol):
    caps = _make_caps(Protocol.UNIVERSAL)
    caps.protocol = bad_protocol
    with pytest.raises(PrintError) as ei:
        build_adapter(caps)
    assert ei.value.code == PrintError.PROTOCOL_UNSUPPORTED


@pytest.mark.parametrize("proto,expected", [
    (Protocol.UNIVERSAL, UniversalWindowsAdapter),
    (Protocol.ZPL, ZPLAdapter),
    (Protocol.TSPL, TSPLAdapter),
    (Protocol.CPCL, CPCLAdapter),
])
def test_build_adapter_dispatch_explicit(proto, expected):
    adapter = build_adapter(_make_caps(proto))
    assert isinstance(adapter, expected)


def test_protocol_basis_no_brand_inference():
    zebra = _make_caps(Protocol.ZPL)
    zebra.driver = DriverInfo(driver_name="Zebra ZD420 Printer")
    generic = _make_caps(Protocol.ZPL)
    generic.driver = DriverInfo(driver_name="Generic Text Only")
    basis_z = protocol_selection_basis(zebra)
    basis_g = protocol_selection_basis(generic)
    # 声明协议相同 → 依据文案必须一致：不读厂商名/驱动名做断言
    assert basis_z == basis_g
    assert "未按厂商名/驱动名推断" in basis_z
    assert "Zebra" not in basis_z


def test_protocol_basis_reports_unverified():
    caps = _make_caps(Protocol.TSPL)
    assert caps.verified_with_hardware is False
    basis = protocol_selection_basis(caps)
    assert "未经真实设备实测" in basis
