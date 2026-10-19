"""windows_print / qt_print 后端可用性与纯函数测试（Linux 可跑）。

平台不可用分支直接断言抛 PrintError，禁止 mock 假装调用成功。
运行方式（仓库根目录）：
    python3 -m pytest tests/test_windows_print.py -q
"""

import pytest

from client.printing import qt_print
from client.printing import windows_print as wp
from client.printing.capabilities import Transport
from client.printing.errors import PrintError


# ---------------------------------------------------------------------------
# 可用性：Linux 上两个后端均不可用，模块本身可导入（不依赖 pywin32/PySide6）
# ---------------------------------------------------------------------------
def test_windows_print_available_false_on_linux():
    assert wp.windows_print_available() is False


def test_qt_print_available_false_on_linux():
    assert qt_print.qt_print_available() is False


# ---------------------------------------------------------------------------
# parse_printer_info_2：端口 -> Transport 全覆盖
# ---------------------------------------------------------------------------
def test_parse_usb_port():
    caps = wp.parse_printer_info_2({
        "name": "Zebra ZD420", "driver": "ZDesigner ZD420",
        "port": "USB001", "status_bits": 0,
    })
    assert caps.transport == Transport.USB
    assert caps.status.available is True
    assert caps.driver.driver_name == "ZDesigner ZD420"


def test_parse_network_ip_port():
    caps = wp.parse_printer_info_2({
        "name": "Network Label", "driver": "Generic",
        "port": "IP_10.0.0.5", "status_bits": 0,
    })
    assert caps.transport == Transport.NETWORK


def test_parse_unc_share_port():
    caps = wp.parse_printer_info_2({
        "name": "Shared Queue", "driver": "PCL6",
        "port": r"\\PRINT-SERVER\LABEL-SHARE", "status_bits": 0,
    })
    assert caps.transport == Transport.NETWORK


def test_parse_lpt_local_port():
    caps = wp.parse_printer_info_2({
        "name": "Old Deskjet", "driver": "HP",
        "port": "LPT1:", "status_bits": 0,
    })
    assert caps.transport == Transport.LOCAL


def test_parse_bluetooth_port():
    caps = wp.parse_printer_info_2({
        "name": "BT Printer", "driver": "BT Driver",
        "port": "BT001", "status_bits": 0,
    })
    assert caps.transport == Transport.BLUETOOTH
    caps2 = wp.parse_printer_info_2({
        "name": "BT Printer 2", "driver": "BT Driver",
        "port": "Bluetooth", "status_bits": 0,
    })
    assert caps2.transport == Transport.BLUETOOTH


# ---------------------------------------------------------------------------
# parse_printer_info_2：状态位
# ---------------------------------------------------------------------------
def test_parse_offline_bit():
    caps = wp.parse_printer_info_2({
        "name": "P1", "port": "USB001", "status_bits": 0x0001,
    })
    assert caps.status.offline is True
    assert caps.status.available is False


def test_parse_error_bit():
    caps = wp.parse_printer_info_2({
        "name": "P1", "port": "USB001", "status_bits": 0x0002,
    })
    assert caps.status.error is True
    assert caps.status.available is False


def test_parse_paused_bit():
    caps = wp.parse_printer_info_2({
        "name": "P1", "port": "USB001", "status_bits": 0x0004,
    })
    assert caps.status.paused is True
    assert caps.status.available is False


def test_parse_printing_bit_keeps_available():
    caps = wp.parse_printer_info_2({
        "name": "P1", "port": "USB001", "status_bits": 0x0008,
    })
    assert caps.status.busy is False
    assert caps.status.available is True


def test_parse_busy_bit_keeps_available():
    caps = wp.parse_printer_info_2({
        "name": "P1", "port": "USB001", "status_bits": 0x0200,
    })
    assert caps.status.busy is True
    assert caps.status.available is True


def test_parse_default_flag_and_driver_version():
    caps = wp.parse_printer_info_2({
        "name": "Default Queue", "driver": "Vendor Drv",
        "port": "IP_192.168.1.20", "status_bits": 0,
        "is_default": True, "driver_version": "4.1.2",
    })
    assert caps.is_default is True
    assert caps.driver.driver_version == "4.1.2"


def test_parse_win32_field_names():
    caps = wp.parse_printer_info_2({
        "pPrinterName": "Win32 Queue",
        "pDriverName": "PCL5e",
        "pPortName": "USB002",
        "pStatus": 0,
    })
    assert caps.printer_name == "Win32 Queue"
    assert caps.transport == Transport.USB


# ---------------------------------------------------------------------------
# map_win_error 映射表（纯函数）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("winerror, code", [
    (5, PrintError.ACCESS_DENIED),
    (1801, PrintError.QUEUE_MISSING),
    (1802, PrintError.QUEUE_MISSING),
    (1812, PrintError.DRIVER_MISSING),
    (3019, PrintError.DRIVER_MISSING),
    (1797, PrintError.DRIVER_MISSING),
    (9999, PrintError.SPOOL_FAILED),
])
def test_map_win_error(winerror, code):
    err = wp.map_win_error(winerror, "测试操作")
    assert err.code == code


# ---------------------------------------------------------------------------
# 平台不可用分支：直接断言抛 PrintError（不 mock 成功）
# ---------------------------------------------------------------------------
def test_spool_raw_data_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.spool_raw_data("FakeQueue", b"\x00", "doc")
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_list_printers_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.list_printers()
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_get_default_printer_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.get_default_printer()
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_get_driver_info_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.get_driver_info("FakeQueue")
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_cancel_job_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.cancel_job("FakeQueue", 1)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_query_job_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        wp.query_job("FakeQueue", 1)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_qt_list_printers_raises_on_linux():
    with pytest.raises(PrintError) as exc:
        qt_print.list_qt_printers()
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM


def test_qt_print_pdf_raises_on_linux():
    from client.printing.job import LabelJobSpec
    spec = LabelJobSpec(pdf_path="x.pdf")
    with pytest.raises(PrintError) as exc:
        qt_print.print_pdf_via_qt("FakeQueue", "x.pdf", spec)
    assert exc.value.code == PrintError.UNSUPPORTED_PLATFORM
