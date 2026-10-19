"""打印机能力配置模型（纯数据层，与后端/协议解耦）。

所有尺寸单位统一为毫米（mm）。本模块只定义数据结构与取值域，
不执行任何系统调用，因此可在任意平台（含 Linux 测试环境）导入。

字段语义约定（全项目统一）：
- ``protocol`` 表示本适配器向设备发送内容的语言：
  ``universal`` = 通过 Windows 驱动与打印队列的通用打印；
  ``zpl`` / ``tspl`` / ``cpcl`` = 专用标签指令协议。
- ``verified_with_hardware`` 只有在存在真实设备测试记录
  （见 docs/PRINTER_TEST_REPORT.md）时才允许为 True；
  未经实测一律 False，调用方不得伪造。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Tuple


class Transport(str, Enum):
    """设备物理连接方式。USB / 网络 / 蓝牙必须分别处理，
    不得假定所有设备具有相同传输接口。"""

    LOCAL = "local"          # 本机队列（并口/串口/系统虚拟端口）
    USB = "usb"
    NETWORK = "network"      # TCP/IP（LPR、9100、网络共享队列等）
    BLUETOOTH = "bluetooth"
    UNKNOWN = "unknown"


class Protocol(str, Enum):
    UNIVERSAL = "universal"  # Windows 驱动 + 打印队列（默认、最通用）
    ZPL = "zpl"              # Zebra Programming Language II
    TSPL = "tspl"            # TSC / 立象 等兼容指令
    CPCL = "cpcl"            # Comtec Printer Control Language（Honeywell/Star 等）


class PrintQuality(str, Enum):
    DRAFT = "draft"
    NORMAL = "normal"
    HIGH = "high"


class JobOrientation(str, Enum):
    PORTRAIT = "portrait"
    LANDSCAPE = "landscape"


@dataclass(frozen=True)
class PaperSize:
    """纸张/标签规格。宽度与高度均为物理毫米，打印时按 1:1 尺寸输出。"""

    name: str
    width_mm: float
    height_mm: float

    @property
    def is_valid(self) -> bool:
        return self.width_mm > 0 and self.height_mm > 0

    def to_dict(self) -> dict:
        return {"name": self.name, "width_mm": self.width_mm,
                "height_mm": self.height_mm}

    @classmethod
    def from_dict(cls, d: dict) -> "PaperSize":
        return cls(name=str(d.get("name", "")),
                   width_mm=float(d.get("width_mm", 0.0)),
                   height_mm=float(d.get("height_mm", 0.0)))


@dataclass(frozen=True)
class PrinterStatus:
    """打印机状态。available=False 表示当前不可用（离线/错误/暂停/忙碌）。"""

    available: bool = True
    offline: bool = False
    error: bool = False
    paused: bool = False
    busy: bool = False
    raw_codes: Tuple[int, ...] = ()

    def describe(self) -> str:
        """给用户看的简短状态描述。"""
        if self.offline:
            return "离线"
        if self.error:
            return "错误"
        if self.paused:
            return "已暂停"
        if self.busy:
            return "忙碌"
        return "就绪" if self.available else "不可用"


@dataclass(frozen=True)
class DriverInfo:
    """打印机队列对应的驱动信息。"""

    driver_name: str | None = None       # win32print PRINTER_INFO_2.pDriverName
    driver_version: str | None = None
    driver_path: str | None = None       # 驱动文件路径（Windows 侧）
    is_vendor_driver: bool = True        # 是否厂商原生驱动（非通用文本驱动）
    signature_verified: bool | None = None  # 驱动签名校验结果，未知为 None


@dataclass
class PrinterCapabilities:
    """一台打印机/队列的能力配置模型。

    DPI、可打印区域、纸张尺寸、标签间隙、偏移与可用参数全部收敛到此处，
    通用打印与专用协议适配器共用同一份描述，禁止各适配器私下定义能力。
    """

    printer_name: str
    driver: DriverInfo = field(default_factory=DriverInfo)
    port_name: str | None = None
    is_default: bool = False
    status: PrinterStatus | None = None
    transport: Transport = Transport.UNKNOWN
    protocol: Protocol = Protocol.UNIVERSAL
    # 分辨率：优先 dpi_x/dpi_y（可能不等），缺省时回退 dpi
    dpi: int | None = None
    dpi_x: int | None = None
    dpi_y: int | None = None
    supported_papers: Tuple[PaperSize, ...] = ()
    max_printable_width_mm: float | None = None
    max_printable_height_mm: float | None = None
    supports_custom_paper: bool = False
    supports_true_size: bool = False      # 是否支持按 1:1 物理尺寸打印
    needs_vendor_driver: bool = False
    verified_with_hardware: bool = False  # 是否经真实设备实测（严禁伪造）
    notes: str = ""

    # ---- 派生 ----
    def resolution(self) -> int | None:
        if self.dpi is not None:
            return self.dpi
        if self.dpi_x and self.dpi_y and self.dpi_x == self.dpi_y:
            return self.dpi_x
        return None

    def can_print_size(self, paper: PaperSize,
                       tolerance_mm: float = 0.5) -> bool:
        """给定纸张是否在可打印区域能力之内（含容差）。"""
        if not paper.is_valid:
            return False
        if self.max_printable_width_mm is not None and \
                paper.width_mm > self.max_printable_width_mm + tolerance_mm:
            return False
        if self.max_printable_height_mm is not None and \
                paper.height_mm > self.max_printable_height_mm + tolerance_mm:
            return False
        return True

    def find_paper(self, width_mm: float, height_mm: float,
                   tolerance_mm: float = 0.5) -> PaperSize | None:
        """在 supported_papers 中查找与给定尺寸接近的规格。"""
        for p in self.supported_papers:
            if abs(p.width_mm - width_mm) <= tolerance_mm and \
                    abs(p.height_mm - height_mm) <= tolerance_mm:
                return p
        return None

    def to_dict(self) -> dict:
        return {
            "printer_name": self.printer_name,
            "driver_name": self.driver.driver_name if self.driver else None,
            "port_name": self.port_name,
            "is_default": self.is_default,
            "status": None if self.status is None else {
                "available": self.status.available,
                "offline": self.status.offline,
                "error": self.status.error,
                "paused": self.status.paused,
                "busy": self.status.busy,
                "raw_codes": list(self.status.raw_codes),
            },
            "transport": self.transport.value,
            "protocol": self.protocol.value,
            "dpi": self.dpi,
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
            "supported_papers": [p.to_dict() for p in self.supported_papers],
            "max_printable_width_mm": self.max_printable_width_mm,
            "max_printable_height_mm": self.max_printable_height_mm,
            "supports_custom_paper": self.supports_custom_paper,
            "supports_true_size": self.supports_true_size,
            "needs_vendor_driver": self.needs_vendor_driver,
            "verified_with_hardware": self.verified_with_hardware,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PrinterCapabilities":
        caps = cls(printer_name=str(d.get("printer_name", "")))
        caps.driver = DriverInfo(driver_name=d.get("driver_name"))
        caps.port_name = d.get("port_name")
        caps.is_default = bool(d.get("is_default", False))
        st = d.get("status")
        if isinstance(st, dict):
            caps.status = PrinterStatus(
                available=bool(st.get("available", True)),
                offline=bool(st.get("offline", False)),
                error=bool(st.get("error", False)),
                paused=bool(st.get("paused", False)),
                busy=bool(st.get("busy", False)),
                raw_codes=tuple(int(x) for x in st.get("raw_codes", [])),
            )
        caps.transport = Transport(d.get("transport", "unknown"))
        caps.protocol = Protocol(d.get("protocol", "universal"))
        caps.dpi = d.get("dpi")
        caps.dpi_x = d.get("dpi_x")
        caps.dpi_y = d.get("dpi_y")
        caps.supported_papers = tuple(
            PaperSize.from_dict(p) for p in d.get("supported_papers", []))
        caps.max_printable_width_mm = d.get("max_printable_width_mm")
        caps.max_printable_height_mm = d.get("max_printable_height_mm")
        caps.supports_custom_paper = bool(d.get("supports_custom_paper", False))
        caps.supports_true_size = bool(d.get("supports_true_size", False))
        caps.needs_vendor_driver = bool(d.get("needs_vendor_driver", False))
        caps.verified_with_hardware = bool(d.get("verified_with_hardware", False))
        caps.notes = str(d.get("notes", ""))
        return caps


def infer_transport_from_port(port_name: str | None) -> Transport:
    """按 Windows 端口名推断连接方式（纯函数，可测）。

    - USB 端口：USB001/002…、usb# 前缀（PnP 设备路径）
    - 网络端口：IP_xxx.xxx.xxx.xxx、UNC 共享名（双反斜杠加主机名）
    - 蓝牙：BT 前缀 / Bluetooth
    - 其余（LPTx、COMx、FILE:、PDF: 等）归 LOCAL / UNKNOWN
    """
    if not port_name:
        return Transport.UNKNOWN
    p = port_name.strip().lower()
    if p.startswith("usb") or "usb#" in p:
        return Transport.USB
    if p.startswith("ip_") or p.startswith("\\\\") or p.startswith("tcpip"):
        return Transport.NETWORK
    if p.startswith("bt") or "bluetooth" in p:
        return Transport.BLUETOOTH
    if p.startswith(("lpt", "com", "file:", "pdf:")) or ":" in p:
        return Transport.LOCAL
    return Transport.UNKNOWN
