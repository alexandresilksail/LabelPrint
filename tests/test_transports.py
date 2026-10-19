"""传输层测试：纯配置校验、端点解析、Linux 平台分支（不建立真实连接）。

不依赖 A 切片；SpoolChannel/UsbChannel/BluetoothChannel 在 Linux 仅断言
UNSUPPORTED_PLATFORM 分支，不用 mock 假装连接成功。
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
# 纯配置校验（host 合法性 / 端口 1–65535 / 非空）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("host,port", [
    ("", 9100),
    ("   ", 9100),
    ("192.168.1. 20", 9100),     # 内部空白 → 非法
    ("192.168.1.20", 0),
    ("192.168.1.20", 65536),
    ("192.168.1.20", True),       # bool 拒绝
    ("192.168.1.20", 9100.5),    # 浮点拒绝
])
def test_invalid_network_config(host, port):
    with pytest.raises(PrintError) as ei:
        validate_network_config(host, port)
    assert ei.value.code == PrintError.INVALID_JOB


def test_valid_network_config_normalized():
    assert validate_network_config(" 192.168.1.20 ", 9100) == \
        ("192.168.1.20", 9100)


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


# ---------------------------------------------------------------------------
# build_channel 分发与 Linux 抛错分支
# ---------------------------------------------------------------------------

def _caps(transport, port_name=None, name="Q"):
    return PrinterCapabilities(
        printer_name=name, transport=transport, port_name=port_name)


def test_build_network_channel_without_connecting():
    ch = build_channel(_caps(Transport.NETWORK, "192.168.1.20:9100"))
    assert isinstance(ch, NetworkChannel)
    assert ch.host == "192.168.1.20"
    assert ch.port == 9100
    assert ch.is_open() is False  # 未 open，不建立连接


def test_build_local_spool_channel_constructs_on_linux():
    ch = build_channel(_caps(Transport.LOCAL, name="MyQueue"))
    assert isinstance(ch, SpoolChannel)


def test_usb_channel_unsupported_on_linux():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.USB, "USB001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_bluetooth_channel_unsupported_on_linux():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.BLUETOOTH, "BT001"))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_unknown_transport_unsupported():
    with pytest.raises(PrintError) as ei:
        build_channel(_caps(Transport.UNKNOWN))
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


# ---------------------------------------------------------------------------
# 各通道 Linux 分支（不 mock 后端）
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


def test_usb_channel_open_unsupported_on_linux():
    with pytest.raises(PrintError) as ei:
        UsbChannel(r"\\?\usb#vid_test").open()
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_bluetooth_channel_open_unsupported_on_linux():
    with pytest.raises(PrintError) as ei:
        BluetoothChannel("COM5").open()
    assert ei.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_spool_channel_requires_queue_name():
    with pytest.raises(PrintError) as ei:
        SpoolChannel("")
    assert ei.value.code == PrintError.INVALID_JOB
