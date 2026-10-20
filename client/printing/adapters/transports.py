"""传输层抽象：把"指令字节"送到物理/逻辑打印通道。

平台与实测状态（诚信声明）：
- 本模块全部实现**未经真实设备实测**（verified_with_hardware=False），
  实测流程见 docs/PRINTER_TEST_REPORT.md。
- :class:`NetworkChannel`（TCP 9100 raw socket）跨平台可构造、配置可校验，
  但仅在公开协议（JetDirect/9100）规范上实现，未对接真实网络打印机；
  发送成功只代表"指令已送达内核 socket"，**无设备确认回执**。
- :class:`SpoolChannel` 仅支持 **Windows**（惰性 import
  ``client.printing.windows_print.spool_raw_data``，A 切片集成）；Linux 构造
  允许，调用 open/write 抛 ``UNSUPPORTED_PLATFORM``。
- :class:`UsbChannel` / :class:`BluetoothChannel` **未集成**：无论何种平台，
  ``open()``/``write()`` 一律抛 ``TRANSPORT_NOT_IMPLEMENTED``，禁止伪装可用。
- LPR（RFC 1179，515 端口）**未实现**：build_channel 遇到 LPR 需求直接抛
  ``TRANSPORT_NOT_IMPLEMENTED``；网络通道只做 9100 TCP raw。

纯配置校验（host 合法性、端口 1–65535、timeout>0）不触碰系统，可在
Linux 直接测试。
"""

from __future__ import annotations

import socket
import sys
from abc import ABC, abstractmethod

from ..capabilities import PrinterCapabilities, Transport
from ..errors import PrintError


def is_windows() -> bool:
    """当前是否为 Windows 平台（纯函数，便于打补丁测试）。"""
    return sys.platform == "win32"


# ---------------------------------------------------------------------------
# 纯配置校验（Linux 可测，不抛异常，返回问题字符串列表）
# ---------------------------------------------------------------------------

def validate_network_config(
    host: str, port: int, timeout: float = 5.0
) -> list[str]:
    """纯函数校验网络通道配置，返回问题描述列表（空列表 = 合法）。

    - host 非空、不含空白字符、长度 ≤253（域名/IPv4 基本合法性）；
    - port 为 int 且 1–65535（拒绝 bool 与浮点）；
    - timeout 为正数秒（拒绝 bool / 非数值 / ≤0）。
    """
    problems: list[str] = []

    if host is None or not str(host).strip():
        problems.append("网络打印机地址（host）不能为空")
    else:
        h = str(host).strip()
        if any(ch.isspace() for ch in h) or len(h) > 253:
            problems.append(
                f"非法的打印机地址: {h!r}（请填写 IP 或主机名，"
                "不含空格，长度 ≤253）"
            )

    if isinstance(port, bool) or not isinstance(port, int) \
            or not (1 <= port <= 65535):
        problems.append(f"网络端口必须为 1–65535 的整数（当前 {port!r}）")

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or timeout <= 0:
        problems.append(f"连接超时必须为正数秒（当前 {timeout!r}）")

    return problems


def parse_network_endpoint(port_name: str | None) -> tuple[str, int]:
    """从能力配置的 ``port_name`` 解析 (host, port)，非法抛 PrintError。

    接受：``IP_192.168.1.1:9100``、``192.168.1.1:9100``、``192.168.1.1``
    （缺省 9100，Zebra/TSC/Honeywell 网络打印机 raw 端口）。
    LPR（``lpr://`` / ``lpr:`` 方案）在此即判未集成。
    """
    if not port_name or not str(port_name).strip():
        raise PrintError(
            PrintError.INVALID_JOB,
            "网络打印机缺少地址（port_name 为空）",
            resolution="请在端口配置中填写 IP:端口，例如 192.168.1.20:9100",
        )
    raw = str(port_name).strip()
    low = raw.lower()
    if low.startswith(("lpr://", "lpr:")):
        raise PrintError(
            PrintError.TRANSPORT_NOT_IMPLEMENTED,
            "LPR（RFC 1179）打印服务尚未集成，本通道仅支持 9100 TCP raw",
            resolution="请改用 9100 raw 端口直连，或经 Windows 厂商队列打印",
        )
    if low.startswith("ip_"):
        raw = raw[3:]
    # 同时支持 IPv6 字面量的简单场景用 rsplit（冒号最后一段为端口）
    if ":" in raw:
        host, _, port_str = raw.rpartition(":")
        try:
            port = int(port_str)
        except ValueError:
            raise PrintError(
                PrintError.INVALID_JOB,
                f"非法端口号: {port_str!r}",
            ) from None
    else:
        host, port = raw, 9100

    problems = validate_network_config(host, port)
    if problems:
        raise PrintError(
            PrintError.INVALID_JOB,
            "；".join(problems),
            resolution="请填写 IP 或主机名与 1–65535 端口",
        )
    return str(host).strip(), port


# ---------------------------------------------------------------------------
# 通道抽象
# ---------------------------------------------------------------------------

class TransportChannel(ABC):
    """打印通道抽象。所有方法失败统一抛 :class:`PrintError`。

    生命周期：``open()`` → ``write(data)``* → ``flush()`` → ``close()``；
    ``is_open()`` 反映当前句柄状态。``close()`` 必须幂等。

    ``kind`` 只读类属性标识通道类型（``spool``/``tcp``/``usb``/
    ``bluetooth``/``lpr``），适配器据此诚实判定 SENT / QUEUED 语义。
    """

    #: 通道类型标识（子类覆盖为 "spool"/"tcp"/"usb"/"bluetooth"/"lpr"）
    kind: str = "unknown"

    @abstractmethod
    def open(self) -> None:
        """建立连接/打开句柄。"""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """写入指令字节（raw，不做协议转换）。"""

    @abstractmethod
    def flush(self) -> None:
        """冲刷缓冲，确保数据全部送达。"""

    @abstractmethod
    def close(self) -> None:
        """释放句柄/断开连接（幂等）。"""

    @abstractmethod
    def is_open(self) -> bool:
        """通道当前是否可用。"""


# ---------------------------------------------------------------------------
# 网络通道（TCP 9100 raw socket；LPR 未实现）
# ---------------------------------------------------------------------------

class NetworkChannel(TransportChannel):
    """TCP 9100 raw socket（JetDirect/Port 9100）。

    支持平台：Windows / Linux / macOS（socket 原生）。
    实测状态：未实测。发送成功无设备 ACK，语义为 SENT（≠物理打印成功）。
    LPR（RFC 1179，515 端口）未实现，仅文档说明。
    """

    DEFAULT_PORT = 9100
    kind = "tcp"

    def __init__(self, host: str, port: int = DEFAULT_PORT,
                 timeout: float = 5.0):
        problems = validate_network_config(host, port, timeout)
        if problems:
            raise PrintError(
                PrintError.INVALID_JOB,
                "；".join(problems),
                resolution="请填写 IP 或主机名与 1–65535 端口，超时为正数秒",
            )
        self.host = str(host).strip()
        self.port = int(port)
        self.timeout = float(timeout)
        self._sock: socket.socket | None = None

    def open(self) -> None:
        if self._sock is not None:
            return
        try:
            self._sock = socket.create_connection(
                (self.host, self.port), timeout=self.timeout)
        except OSError as exc:
            self._sock = None
            raise PrintError(
                PrintError.SPOOL_FAILED,
                f"无法连接网络打印机 {self.host}:{self.port}",
                resolution="检查打印机电源、网络与 IP/端口（raw 默认 9100）",
                cause=exc,
            ) from exc

    def write(self, data: bytes) -> None:
        if self._sock is None:
            self.open()
        assert self._sock is not None
        try:
            self._sock.sendall(data)
        except OSError as exc:
            raise PrintError(
                PrintError.SPOOL_FAILED,
                f"向 {self.host}:{self.port} 写入指令失败",
                cause=exc,
            ) from exc

    def flush(self) -> None:
        # TCP 流式 socket 无用户态缓冲，sendall 已送达内核；保留接口一致性。
        return None

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def is_open(self) -> bool:
        return self._sock is not None


# ---------------------------------------------------------------------------
# Windows 本机队列通道（惰性 import A 切片 spool_raw_data）
# ---------------------------------------------------------------------------

class SpoolChannel(TransportChannel):
    """Windows 本机打印队列通道（并口/串口/系统队列/共享队列）。

    支持平台：**仅 Windows**。
    实测状态：未实测。惰性 import ``client.printing.windows_print.spool_raw_data``
    （A 切片，签名冻结：``spool_raw_data(printer_name, data, doc_name)
    -> PrintResult``）。
    """

    kind = "spool"

    def __init__(self, printer_name: str, doc_name: str = "Label"):
        if not printer_name or not str(printer_name).strip():
            raise PrintError(
                PrintError.INVALID_JOB,
                "SpoolChannel 需要非空的打印机队列名",
            )
        self.printer_name = str(printer_name).strip()
        self.doc_name = doc_name or "Label"
        self._open = False

    def open(self) -> None:
        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "本机队列通道仅支持 Windows",
                resolution="当前为非 Windows 环境，无法访问系统打印队列",
            )
        self._open = True

    def write(self, data: bytes) -> None:
        if not self._open:
            self.open()
        try:
            from ..windows_print import spool_raw_data
        except ImportError as exc:
            raise PrintError(
                PrintError.WIN32PRINT_UNAVAILABLE,
                "缺少 Windows 队列后端（windows_print.spool_raw_data）",
                resolution="该后端由 A 切片在 Windows 环境提供，集成阶段重跑",
                cause=exc,
            ) from exc
        spool_raw_data(self.printer_name, data, self.doc_name)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open


# ---------------------------------------------------------------------------
# USB 通道（未集成：open/write 一律 TRANSPORT_NOT_IMPLEMENTED）
# ---------------------------------------------------------------------------

class UsbChannel(TransportChannel):
    r"""USB 打印机设备通道（**未集成**，禁止伪装可用）。

    计划经 Windows pywin32 ``win32file.CreateFile`` 打开 PnP 设备路径
    （如 ``\\?\usb#vid_xxxx&pid_xxxx#...``），当前 raw 读写未集成；
    无论 Windows / 非 Windows，``open()``/``write()`` 一律抛
    ``TRANSPORT_NOT_IMPLEMENTED``。
    """

    kind = "usb"

    def __init__(self, device_path: str | None):
        self.device_path = device_path or ""
        self._open = False

    def _not_implemented(self) -> None:
        raise PrintError(
            PrintError.TRANSPORT_NOT_IMPLEMENTED,
            "USB 直连通道尚未集成，请改用 Windows 队列（厂商驱动）打印",
            resolution="集成阶段经 win32file 打开 USB PnP 设备路径后启用；"
                       "现阶段请把设备接入 Windows 厂商驱动队列",
        )

    def open(self) -> None:
        self._not_implemented()

    def write(self, data: bytes) -> None:
        self._not_implemented()

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open


# ---------------------------------------------------------------------------
# 蓝牙通道（未集成：open/write 一律 TRANSPORT_NOT_IMPLEMENTED）
# ---------------------------------------------------------------------------

class BluetoothChannel(TransportChannel):
    r"""蓝牙通道（虚拟 COM / RFCOMM，**未集成**，禁止伪装可用）。

    计划经 Windows 系统虚拟 COM 端口（\\.\COMx）或 RFCOMM SPP 套接字收发，
    当前未集成；无论 Windows / 非 Windows，``open()``/``write()`` 一律抛
    ``TRANSPORT_NOT_IMPLEMENTED``。
    """

    kind = "bluetooth"

    def __init__(self, port_name: str | None):
        self.port_name = port_name or ""
        self._open = False

    def _not_implemented(self) -> None:
        raise PrintError(
            PrintError.TRANSPORT_NOT_IMPLEMENTED,
            "蓝牙直连通道尚未集成，请改用 Windows 队列（厂商驱动）打印",
            resolution="集成阶段经虚拟 COM 或 RFCOMM SPP 补全；"
                       "现阶段请改用网络 9100 或 Windows 厂商队列",
        )

    def open(self) -> None:
        self._not_implemented()

    def write(self, data: bytes) -> None:
        self._not_implemented()

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------

def build_channel(capabilities: PrinterCapabilities) -> TransportChannel:
    """按 ``capabilities.transport`` 选择并构造通道（不主动 open）。

    - network：仅 9100 TCP raw；LPR 需求抛 ``TRANSPORT_NOT_IMPLEMENTED``；
    - local：Windows 本机队列（Linux 可构造，open 抛 UNSUPPORTED_PLATFORM）；
    - usb / bluetooth：非 Windows 抛 ``UNSUPPORTED_PLATFORM``（平台说明），
      即便 Windows 也抛 ``TRANSPORT_NOT_IMPLEMENTED``（未集成）；
    - 未知连接方式抛 ``UNSUPPORTED_PLATFORM``。
    """
    t = capabilities.transport
    if t is Transport.NETWORK:
        host, port = parse_network_endpoint(capabilities.port_name)
        return NetworkChannel(host, port)
    if t is Transport.LOCAL:
        return SpoolChannel(capabilities.printer_name)
    if t is Transport.USB:
        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "USB 直连通道在当前非 Windows 平台不可用",
                resolution="USB 设备请接入 Windows，经厂商驱动队列打印；"
                           "USB 直连 raw 通道本身尚未集成",
            )
        # Windows 上同样未集成，如实报错，禁止伪装可用
        UsbChannel(capabilities.port_name).open()
    if t is Transport.BLUETOOTH:
        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "蓝牙直连通道在当前非 Windows 平台不可用",
                resolution="蓝牙设备请改用网络 9100 或 Windows 厂商队列；"
                           "蓝牙直连 raw 通道本身尚未集成",
            )
        BluetoothChannel(capabilities.port_name).open()
    raise PrintError(
        PrintError.UNSUPPORTED_PLATFORM,
        f"未知/不支持的连接方式: {t.value}",
        resolution="支持 network(TCP 9100) / local(本机队列)；"
                   "usb / bluetooth / LPR 尚未集成",
    )
