"""传输层测试：纯配置校验、端点解析、通道 kind、Linux 平台分支（不建立真实连接）。

不依赖 A 切片；SpoolChannel 在 Linux 仅断言 UNSUPPORTED_PLATFORM 分支；
UsbChannel/BluetoothChannel 无论平台一律 TRANSPORT_NOT_IMPLEMENTED，
不用 mock 假装连接成功。
"""

import pytest

from client.printing.adapters.transports import (
    BluetoothChannel, NetworkChannel, SpoolChannel, UsbChannel,
    build_channel, parse_network_endpoint, validate_network_config,
)
from client.printing.capabilities import (
    PrinterCapabilities, Transport,
)
from client.printing.errors import PrintError


# ---------------------------------------------------------------------------
# 纯配置校验（host 合法性 / 端口 1–65535 / timeout>0；返回问题列表，不抛异常）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host,port,timeout", [
    ("", 9100, 5.0),
    ("   ", 9100, 5.0),
    ("192.168.1. 20", 9100, 5.0),     # 内部空白 → 非法
    ("192.168.1.20", 0, 5.0),
    ("192.168.1.20", 65536, 5.0),
    ("192.168.1.20", True, 5.0),       # bool 拒绝
    ("192.168.1.20", 9100.5, 5.0),     # 浮点拒绝
    ("192.168.1.20", 9100, 0),         # 超时非正
    ("192.168.1.20", 9100, -1.0),
    ("192.168.1.20", 9100, True),      # bool 超时拒绝
])
def test_invalid_network_config_reported(host, port, timeout):
    problems = validate_network_config(host, port, timeout)
    assert problems, "非法配置必须返回非空问题列表"


def test_valid_network_config_empty_problems():
    assert validate_network_config("192.168.1.20", 9100, 5.0) == []


def test_network_channel_constructor_raises_on_bad_config():
    with pytest.raises(PrintError) as ei:
        NetworkChannel("192.168.1.20", 0, 5.0)
    assert ei.value.code == PrintError.INVALID_JOB


# ---------------------------------------------------------------------------
# 端点解析
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("port_name,expected", [
    ("IP_192.168.1.20:9100", ("192.168.1.20", 9100)),
    ("192.168.1.20:9100", ("192.168.1.20", 9100)),
    ("10.0.0.5", ("10.0.0.5", 9100)),   # 缺省 9100
])
def test_parse_network_endpoint(port_name, expected):
    assert parse_network_endpoint(port_name) == expected


@pytest.mark.parametrize("port_name", [None, "", "192.168.1.20:abc", " "])
def test_parse_network_endpoint_invalid(port_name):
    with pytest.raises(PrintError) as ei:
        parse_network_endpoint(port_name)
    assert ei.value.code == PrintError.INVALID_JOB


@pytest.mark.parametrize("port_name", ["lpr://192.168.1.20", "lpr:192.168.1.20:515"])
def test_lpr_not_implemented(port_name):
    with pytest.raises(PrintError) as ei:
        parse_network_endpoint(port_name)
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


# ---------------------------------------------------------------------------
# build_channel 分发与 Linux 抛错分支（不连接）
# ---------------------------------------------------------------------------

def _caps(transport, port_name=None, name="Q"):
    return PrinterCapabilities(
        printer_name=name, transport=transport, port_name=port_name)


def test_build_network_channel_without_connecting():
    ch = build_channel(_caps(Transport.NETWORK, "192.168.1.20:9100"))
    assert isinstance(ch, NetworkChannel)
    assert ch.kind == "tcp"
    assert ch.host == "192.168.1.20"
    assert ch.port == 9100
    assert ch.is_open() is False  # 未 open，不建立连接


def test_build_local_spool_channel_constructs_on_linux():
    ch = build_channel(_caps(Transport.LOCAL, name="MyQueue"))
    assert isinstance(ch, SpoolChannel)
    assert ch.kind == "spool"


def test_usb_channel_unsupported_platform_on_linux():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.USB, "USB001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_bluetooth_channel_unsupported_platform_on_linux():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.BLUETOOTH, "BT001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_unknown_transport_unsupported():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.UNKNOWN))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# 各通道 Linux 分支 / 未集成通道（不 mock 后端）
# ---------------------------------------------------------------------------

def test_spool_channel_open_unsupported_on_linux():
    ch = SpoolChannel("MyQueue")
    with pytest.raises(PrintError) as ei:
        ch.open()
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_spool_channel_write_unsupported_on_linux():
    ch = SpoolChannel("MyQueue")
    with pytest.raises(PrintError) as ei:
        ch.write(b"^XA^XZ")
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_usb_channel_open_not_implemented():
    ch = UsbChannel(r"\\?\usb#vid_test")
    assert ch.kind == "usb"
    with pytest.raises(PrintError) as ei:
        ch.open()
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


def test_usb_channel_write_not_implemented():
    ch = UsbChannel(r"\\?\usb#vid_test")
    with pytest.raises(PrintError) as ei:
        ch.write(b"^XA^XZ")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_bluetooth_channel_open_not_implemented():
    ch = BluetoothChannel("COM5")
    assert ch.kind == "bluetooth"
    with pytest.raises(PrintError) as ei:
        ch.open()
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED
    assert "尚未集成" in str(ei.value)


def test_bluetooth_channel_write_not_implemented():
    ch = BluetoothChannel("COM5")
    with pytest.raises(PrintError) as ei:
        ch.write(b"^XA^XZ")
    assert ei.value.code == PrintError.TRANSPORT_NOT_IMPLEMENTED


def test_spool_channel_requires_queue_name():
    with pytest.raises(PrintError) as ei:
        SpoolChannel("")
    assert ei.value.code == PrintError.INVALID_JOB
