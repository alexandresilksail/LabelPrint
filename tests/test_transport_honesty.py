"""传输层诚实状态测试：kind 标识、未集成通道如实报错、SENT/QUEUED 语义。

- 不真实发包：网络只测配置校验与错误映射分支（注入失败 socket / fake channel）；
- USB / 蓝牙 / LPR 一律 TRANSPORT_NOT_IMPLEMENTED，禁止伪装可用；
- TCP 发送成功 = SENT（无设备确认，≠物理打印成功）；spool = QUEUED。
"""

import socket

import pytest

from client.printing.adapters import transports as tx
from client.printing.adapters.transports import (
    BluetoothChannel, NetworkChannel, SpoolChannel, UsbChannel,
    build_channel,
)
from client.printing.adapters.zpl import ZPLAdapter
from client.printing.capabilities import (
    PaperSize, PrinterCapabilities, Protocol, Transport,
)
from client.printing.errors import PrintError
from client.printing.job import JobOutcome, LabelContent, LabelJobSpec


# ---------------------------------------------------------------------------
# kind 只读标识
# ---------------------------------------------------------------------------

def test_channel_kinds():
    assert NetworkChannel("10.0.0.1", 9100).kind == "tcp"
    assert SpoolChannel("Queue").kind == "spool"
    assert UsbChannel("USB001").kind == "usb"
    assert BluetoothChannel("COM5").kind == "bluetooth"


# ---------------------------------------------------------------------------
# USB / 蓝牙：open/write 一律 TRANSPORT_NOT_IMPLEMENTED（不伪装可用）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("channel", [
    UsbChannel(r"\\?\usb#vid_1234"),
    BluetoothChannel("COM5"),
])
def test_usb_bluetooth_open_not_implemented(channel):
    with pytest.raises(PrintError) as ei:
        channel.open()
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


@pytest.mark.parametrize("channel", [
    UsbChannel(r"\\?\usb#vid_1234"),
    BluetoothChannel("COM5"),
])
def test_usb_bluetooth_write_not_implemented(channel):
    with pytest.raises(PrintError) as ei:
        channel.write(b"^XA^XZ")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


# ---------------------------------------------------------------------------
# build_channel：非 Windows USB/BT → UNSUPPORTED_PLATFORM；
# 即便 Windows 也仅 TRANSPORT_NOT_IMPLEMENTED（未集成）
# ---------------------------------------------------------------------------

def _caps(transport, port_name=None, name="Q"):
    return PrinterCapabilities(
        printer_name=name, transport=transport, port_name=port_name)


def test_build_usb_unsupported_platform_on_linux(monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: False)
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.USB, "USB001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_build_bluetooth_unsupported_platform_on_linux(monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: False)
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.BLUETOOTH, "BT001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_build_usb_not_implemented_even_on_windows(monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: True)
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.USB, "USB001"))
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


def test_build_bluetooth_not_implemented_even_on_windows(monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: True)
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.BLUETOOTH, "BT001"))
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_lpr_not_implemented(monkeypatch):
    monkeypatch.setattr(tx, "is_windows", lambda: False)
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.NETWORK, "lpr://192.168.1.20"))
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


# ---------------------------------------------------------------------------
# NetworkChannel：连接/读写错误映射 SPOOL_FAILED（不真实发包）
# ---------------------------------------------------------------------------

def test_network_open_failure_maps_to_spool_failed(monkeypatch):
    def _raise(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(tx.socket, "create_connection", _raise)
    ch = NetworkChannel("10.0.0.9", 9100)
    with pytest.raises(PrintError) as ei:
        ch.open()
    assert ei.value.code == PrintError.SPOOL_FAILED


def test_network_write_failure_maps_to_spool_failed(monkeypatch):
    class _FakeSock:
        def sendall(self, data):
            raise OSError("broken pipe")

        def close(self):
            pass

    monkeypatch.setattr(
        tx.socket, "create_connection", lambda *a, **k: _FakeSock())
    ch = NetworkChannel("10.0.0.9", 9100)
    ch.open()
    with pytest.raises(PrintError) as ei:
        ch.write(b"^XA^XZ")
    assert ei.value.code == PrintError.SPOOL_FAILED


# ---------------------------------------------------------------------------
# submit 按 channel.kind 诚实判定：tcp → SENT，spool → QUEUED
# ---------------------------------------------------------------------------

class _FakeChannel:
    """注入式 fake 通道：只记录写入，不碰硬件。"""

    def __init__(self, kind):
        self.kind = kind
        self.written: bytes | None = None
        self.opened = False

    def open(self):
        self.opened = True

    def write(self, data):
        self.written = data

    def flush(self):
        pass

    def close(self):
        pass

    def is_open(self):
        return self.opened


def _valid_job():
    content = LabelContent(
        items=[("123456789012", "CODE128")],
        label_width_mm=100.0, label_height_mm=60.0,
        barcode_height_mm=15.0, module_mm=0.33, quiet_modules=11,
    )
    return LabelJobSpec(
        paper=PaperSize("L", 100.0, 60.0), copies=1, dpi=203,
        module_mm=0.33, label_content=content,
    )


def _caps_zpl():
    return PrinterCapabilities(
        printer_name="ZPL-TCP", transport=Transport.NETWORK,
        protocol=Protocol.ZPL, dpi=203,
        supported_papers=(PaperSize("L", 100.0, 60.0),),
        max_printable_width_mm=200.0, max_printable_height_mm=200.0,
    )


def test_submit_tcp_channel_returns_sent():
    channel = _FakeChannel("tcp")
    adapter = ZPLAdapter(_caps_zpl(), channel=channel)
    result = adapter.submit(_valid_job())
    assert result.outcome == JobOutcome.SENT
    assert channel.written and b"^XA" in channel.written
    # SENT 语义：无设备确认，绝不声称物理打印成功
    assert "无确认回执" in result.message or "无 ACK" in result.message \
        or "不代表物理打印完成" in result.message
    assert "物理打印成功" not in result.message


def test_submit_spool_channel_returns_queued():
    channel = _FakeChannel("spool")
    adapter = ZPLAdapter(_caps_zpl(), channel=channel)
    result = adapter.submit(_valid_job())
    assert result.outcome == JobOutcome.QUEUED
    assert channel.written and b"^XA" in channel.written


def test_sent_is_not_physically_printed():
    channel = _FakeChannel("tcp")
    adapter = ZPLAdapter(_caps_zpl(), channel=channel)
    result = adapter.submit(_valid_job())
    assert result.outcome is not JobOutcome.PHYSICALLY_PRINTED
