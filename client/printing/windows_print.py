"""Windows 通用打印后端：win32print（pywin32）真实接口封装。

本模块在非 Windows / 未安装 pywin32 的环境（如 Linux 测试机）也必须可导入，
因此 ``win32print`` / ``win32api`` / ``pywintypes`` 全部采用条件导入；
任何真正触发系统调用的入口在环境不满足时抛出 :class:`PrintError`
（UNSUPPORTED_PLATFORM / WIN32PRINT_UNAVAILABLE），绝不静默假成功。

诚信约定：``spool_raw_data`` 只把字节提交到系统打印队列，成功结果一律为
``PrintResult.queued``，文案包含"已提交到打印队列，不代表物理打印完成"，
绝不声称物理打印成功。

Windows API 参考（MS Learn）：
- PRINTER_INFO_2:
  https://learn.microsoft.com/windows/win32/printdocs/printer-info-2
- PRINTER_STATUS_*:
  https://learn.microsoft.com/windows/win32/printdocs/printer-status-constants
- JOB_INFO_1 / JOB_STATUS_*:
  https://learn.microsoft.com/windows/win32/printdocs/job-info-1
- SetJob JOB_CONTROL_DELETE:
  https://learn.microsoft.com/windows/win32/printdocs/setjob
"""

from __future__ import annotations

import sys

from .capabilities import (
    DriverInfo,
    PrinterCapabilities,
    PrinterStatus,
    Protocol,
    infer_transport_from_port,
)
from .errors import PrintError
from .job import JobState, PrintResult

# ---- 条件导入：Linux / 无 pywin32 时模块仍可导入 ----
try:  # pragma: no cover - 仅在 Windows 且安装 pywin32 时命中
    import win32print
    import pywintypes
    _WIN32PRINT = win32print
except ImportError:  # 非 Windows 或未安装 pywin32
    win32print = None  # type: ignore[assignment]
    pywintypes = None  # type: ignore[assignment]
    _WIN32PRINT = None


# ---------------------------------------------------------------------------
# 状态位映射
# ---------------------------------------------------------------------------
# 本切片模拟 PRINTER_INFO_2 字段（parse_printer_info_2 入参 status_bits）采用
# 任务约定的简化位定义，便于在 Linux 上用纯 dict 测试：
#   PRINTER_STATUS_OFFLINE = 0x0001
#   PRINTER_STATUS_ERROR   = 0x0002
#   PRINTER_STATUS_PAUSED  = 0x0004
#   PRINTER_STATUS_PRINTING= 0x0008
#   PRINTER_STATUS_BUSY    = 0x0200
#
# 真实 MS Learn PRINTER_STATUS_* 常量（EnumPrinters 返回的 pStatus）：
#   PRINTER_STATUS_PAUSED        = 0x0001
#   PRINTER_STATUS_ERROR         = 0x0002
#   PRINTER_STATUS_OFFLINE       = 0x0080
#   PRINTER_STATUS_BUSY          = 0x0200
#   PRINTER_STATUS_PRINTING      = 0x0400
#   PRINTER_STATUS_NOT_AVAILABLE = 0x1000
#   PRINTER_STATUS_PROCESSING    = 0x4000
# 注意：真实常量中 0x0001 为 PAUSED，与模拟字段的 OFFLINE=0x0001 不同；
# 真实枚举路径（list_printers）直接读取系统 pStatus，以下 MS 位也一并识别。

_SIM_STATUS_BITS: dict[int, str] = {
    0x0001: "offline",
    0x0002: "error",
    0x0004: "paused",
    0x0008: "printing",
    0x0200: "busy",
}

_MS_STATUS_BITS: dict[int, str] = {
    0x0080: "offline",        # PRINTER_STATUS_OFFLINE
    0x0200: "busy",           # PRINTER_STATUS_BUSY
    0x0400: "printing",       # PRINTER_STATUS_PRINTING
    0x1000: "offline",        # PRINTER_STATUS_NOT_AVAILABLE
    0x4000: "busy",           # PRINTER_STATUS_PROCESSING
}

# JOB_STATUS_*（JOB_INFO_1.Status，MS Learn job-status-constants）
_JOB_STATUS_PAUSED = 0x0001
_JOB_STATUS_ERROR = 0x0002
_JOB_STATUS_DELETING = 0x0004
_JOB_STATUS_SPOOLING = 0x0008
_JOB_STATUS_PRINTING = 0x0010
_JOB_STATUS_PRINTED = 0x0080
_JOB_STATUS_DELETED = 0x0100

# SetJob 控制码（MS Learn SetJob JOB_CONTROL_*）
_JOB_CONTROL_DELETE = 5

# 已知通用/虚拟驱动（非厂商原生驱动）
_GENERIC_DRIVER_MARKERS = ("generic", "text only", "microsoft print to pdf",
                          "xps document writer", "microsoft xps")


def _parse_status(status_bits: int | None) -> PrinterStatus:
    """把 pStatus 位掩码解析为 PrinterStatus（模拟位优先，MS 位补充）。"""
    bits = int(status_bits or 0)
    offline = error = paused = busy = False
    raw: list[int] = []
    table = dict(_MS_STATUS_BITS)
    table.update(_SIM_STATUS_BITS)  # 模拟位覆盖同值 MS 位（0x200 一致）
    for bit, field_name in table.items():
        if bits & bit:
            raw.append(bit)
            if field_name == "offline":
                offline = True
            elif field_name == "error":
                error = True
            elif field_name == "paused":
                paused = True
            elif field_name == "busy":
                busy = True
    available = not (offline or error or paused)
    return PrinterStatus(
        available=available,
        offline=offline,
        error=error,
        paused=paused,
        busy=busy,
        raw_codes=tuple(raw),
    )


def _pick(info: dict, *keys: str):
    for k in keys:
        if k in info and info[k] is not None:
            return info[k]
    return None


# ---------------------------------------------------------------------------
# 可用性守卫
# ---------------------------------------------------------------------------
def windows_print_available() -> bool:
    """当前环境是否可使用 win32print 后端。

    仅当 ``sys.platform == 'win32'`` 且 pywin32 成功导入时返回 True；
    Linux / 未安装 pywin32 一律 False。
    """
    return sys.platform == "win32" and _WIN32PRINT is not None


def _require_win32() -> None:
    """系统调用入口守卫：平台或 pywin32 不满足时抛 PrintError。"""
    if sys.platform != "win32":
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="win32print 后端仅支持 Windows；当前系统无法调用打印队列。",
        )
    if _WIN32PRINT is None:
        raise PrintError(
            PrintError.WIN32PRINT_UNAVAILABLE,
            resolution="缺少 pywin32，请在 Windows 上执行 pip install pywin32 后重试。",
        )


# ---------------------------------------------------------------------------
# 纯函数：PRINTER_INFO_2 dict -> PrinterCapabilities（Linux 可测）
# ---------------------------------------------------------------------------
def parse_printer_info_2(info: dict) -> PrinterCapabilities:
    """把模拟 PRINTER_INFO_2 字段 dict 解析为 :class:`PrinterCapabilities`。

    入参支持两套键名（任取其一）：
    - 模拟/约定键：``name`` / ``driver`` / ``port`` / ``status_bits`` /
      ``attributes`` / ``is_default`` / ``driver_version`` / ``driver_path``
    - win32print PRINTER_INFO_2 原名：``pPrinterName`` / ``pDriverName`` /
      ``pPortName`` / ``pStatus`` / ``pAttributes``

    状态位（模拟约定，见模块顶部映射表 docstring）：
    0x0001 OFFLINE、0x0002 ERROR、0x0004 PAUSED、0x0008 PRINTING、
    0x0200 BUSY；真实 MS 常量 0x80 OFFLINE / 0x400 PRINTING 等同时识别。
    """
    name = _pick(info, "name", "pPrinterName") or ""
    driver_name = _pick(info, "driver", "pDriverName")
    port = _pick(info, "port", "pPortName")
    status_bits = _pick(info, "status_bits", "pStatus")
    is_default = bool(_pick(info, "is_default", "isDefault") or False)

    driver_version = _pick(info, "driver_version", "DriverVersion")
    driver_path = _pick(info, "driver_path", "pDriverPath")
    is_vendor = True
    if driver_name:
        low = str(driver_name).lower()
        is_vendor = not any(m in low for m in _GENERIC_DRIVER_MARKERS)

    driver = DriverInfo(
        driver_name=driver_name,
        driver_version=str(driver_version) if driver_version is not None else None,
        driver_path=driver_path,
        is_vendor_driver=is_vendor,
    )
    return PrinterCapabilities(
        printer_name=str(name),
        driver=driver,
        port_name=port,
        is_default=is_default,
        status=_parse_status(status_bits),
        transport=infer_transport_from_port(port),
        protocol=Protocol.UNIVERSAL,
        verified_with_hardware=False,
        notes="由系统队列枚举得到，未经真实设备实测",
    )


# ---------------------------------------------------------------------------
# Windows 错误码 -> PrintError（纯函数，Linux 可测）
# ---------------------------------------------------------------------------
# Windows 系统错误码（system error codes，MS Learn system-error-codes）：
#   5     ERROR_ACCESS_DENIED
#   1801  ERROR_INVALID_PRINTER_NAME（队列名无效/队列不存在）
#   1802  ERROR_PRINTER_ALREADY_EXISTS（队列注册类失败）
#   1812  指定驱动缺失/映像缺失类错误
#   1797  ERROR_UNKNOWN_PRINTER_DRIVER（未知打印机驱动）
#   3019  打印驱动缺失（print driver missing）
_WIN_ERROR_MAP: dict[int, str] = {
    5: PrintError.ACCESS_DENIED,
    1801: PrintError.QUEUE_MISSING,
    1802: PrintError.QUEUE_MISSING,
    1812: PrintError.DRIVER_MISSING,
    1797: PrintError.DRIVER_MISSING,
    3019: PrintError.DRIVER_MISSING,
}


def map_win_error(winerror: int, action: str) -> PrintError:
    """把 Windows winerror 映射为统一 :class:`PrintError`（纯函数）。

    映射表（来源：MS Learn System Error Codes 与打印子系统错误）：
    - 5 (ERROR_ACCESS_DENIED)            -> ACCESS_DENIED
    - 1801 (ERROR_INVALID_PRINTER_NAME)   -> QUEUE_MISSING
    - 1802 (ERROR_PRINTER_ALREADY_EXISTS)-> QUEUE_MISSING
    - 1812 / 1797 / 3019（驱动缺失类）   -> DRIVER_MISSING
    - 其余未登记错误码                   -> SPOOL_FAILED（保留原始码供排查）
    """
    code = _WIN_ERROR_MAP.get(int(winerror), PrintError.SPOOL_FAILED)
    return PrintError(
        code,
        resolution=f"（Windows 错误 {winerror}，操作：{action}）",
    )


# ---------------------------------------------------------------------------
# 系统调用入口（仅 Windows + pywin32 可用）
# ---------------------------------------------------------------------------
def get_default_printer() -> str | None:
    """返回系统默认打印机名；无默认打印机或调用失败返回 None。

    包装 win32print.GetDefaultPrinter()。平台不满足时抛 PrintError。
    """
    _require_win32()
    try:
        return _WIN32PRINT.GetDefaultPrinter()
    except pywintypes.error:  # type: ignore[misc]
        return None


def get_driver_info(printer_name: str) -> DriverInfo:
    """读取队列驱动信息：OpenPrinter -> GetPrinter(2) -> GetPrinterDriver(2)。

    包装 win32print.GetPrinter / GetPrinterDriver，取 pDriverName、
    DriverVersion、pDriverPath。平台不满足时抛 PrintError。
    """
    _require_win32()
    handle = None
    try:
        handle = _WIN32PRINT.OpenPrinter(printer_name)
        info2 = _WIN32PRINT.GetPrinter(handle, 2)
        driver_name = info2.get("pDriverName")
        try:
            drv = _WIN32PRINT.GetPrinterDriver(handle, None, 2)
            driver_version = drv.get("DriverVersion")
            driver_path = drv.get("DriverPath")
        except pywintypes.error:  # type: ignore[misc]
            driver_version = None
            driver_path = None
        is_vendor = True
        if driver_name:
            low = str(driver_name).lower()
            is_vendor = not any(m in low for m in _GENERIC_DRIVER_MARKERS)
        return DriverInfo(
            driver_name=driver_name,
            driver_version=str(driver_version) if driver_version is not None else None,
            driver_path=driver_path,
            is_vendor_driver=is_vendor,
        )
    except pywintypes.error as exc:  # type: ignore[misc]
        raise map_win_error(exc.winerror, f"读取驱动信息 {printer_name}") from exc
    finally:
        if handle:
            try:
                _WIN32PRINT.ClosePrinter(handle)
            except pywintypes.error:  # type: ignore[misc]
                pass


def list_printers() -> list[PrinterCapabilities]:
    """枚举本机与已连接的网络队列。

    包装 win32print.EnumPrinters(PRINTER_ENUM_LOCAL | PRINTER_ENUM_CONNECTIONS,
    None, 2) 读取 PRINTER_INFO_2（pPrinterName/pDriverName/pPortName/
    pStatus/pAttributes），默认打印机由 GetDefaultPrinter() 比对标记。
    平台不满足时抛 PrintError。
    """
    _require_win32()
    flags = _WIN32PRINT.PRINTER_ENUM_LOCAL | _WIN32PRINT.PRINTER_ENUM_CONNECTIONS
    try:
        default_name = _WIN32PRINT.GetDefaultPrinter()
    except pywintypes.error:  # type: ignore[misc]
        default_name = None

    results: list[PrinterCapabilities] = []
    for info in _WIN32PRINT.EnumPrinters(flags, None, 2):
        caps = parse_printer_info_2({
            "name": info.get("pPrinterName"),
            "driver": info.get("pDriverName"),
            "port": info.get("pPortName"),
            "status_bits": info.get("pStatus"),
            "attributes": info.get("pAttributes"),
            "is_default": info.get("pPrinterName") == default_name,
        })
        # 补充驱动版本（失败不影响枚举主流程）
        try:
            caps.driver = get_driver_info(caps.printer_name)
        except PrintError:
            pass
        results.append(caps)
    return results


def spool_raw_data(printer_name: str, data: bytes,
                   doc_name: str = "Label Document") -> PrintResult:
    """以 RAW 方式把字节流提交到指定队列。

    调用链：OpenPrinter -> StartDocPrinter(RAW) -> StartPagePrinter ->
    WritePrinter（分块循环写尽）-> EndPagePrinter -> EndDocPrinter ->
    ClosePrinter。成功只代表进入系统队列，返回 ``PrintResult.queued``，
    不代表物理打印完成。平台不满足时抛 PrintError。
    """
    _require_win32()
    handle = None
    job_id: int | None = None
    try:
        handle = _WIN32PRINT.OpenPrinter(printer_name)
        job_id = _WIN32PRINT.StartDocPrinter(
            handle, 1, (doc_name, None, "RAW")
        )
        _WIN32PRINT.StartPagePrinter(handle)
        offset = 0
        chunk_size = 4096
        while offset < len(data):
            chunk = data[offset:offset + chunk_size]
            _WIN32PRINT.WritePrinter(handle, chunk)
            offset += len(chunk)
        _WIN32PRINT.EndPagePrinter(handle)
        _WIN32PRINT.EndDocPrinter(handle)
        return PrintResult.queued(
            printer_name, job_id,
            message=(
                f"已提交到打印队列：{printer_name}（任务 #{job_id}）。"
                "已提交到打印队列，不代表物理打印完成，请查看打印机输出。"
            ),
        )
    except pywintypes.error as exc:  # type: ignore[misc]
        err = map_win_error(exc.winerror, f"提交原始数据到 {printer_name}")
        # 文档已开始但中途失败时尝试收尾，忽略收尾错误
        if handle is not None:
            try:
                if job_id:
                    _WIN32PRINT.EndDocPrinter(handle)
            except pywintypes.error:  # type: ignore[misc]
                pass
        return PrintResult.failed(printer_name, err)
    finally:
        if handle is not None:
            try:
                _WIN32PRINT.ClosePrinter(handle)
            except pywintypes.error:  # type: ignore[misc]
                pass


def cancel_job(printer_name: str, job_id: int) -> bool:
    """取消队列任务：OpenPrinter -> SetJob(JOB_CONTROL_DELETE) -> ClosePrinter。

    队列/任务不存在（1801 等）返回 False；其余错误经 map_win_error 抛出。
    平台不满足时抛 PrintError。
    """
    _require_win32()
    handle = None
    try:
        handle = _WIN32PRINT.OpenPrinter(printer_name)
        _WIN32PRINT.SetJob(handle, job_id, 0, None, _JOB_CONTROL_DELETE)
        return True
    except pywintypes.error as exc:  # type: ignore[misc]
        if exc.winerror in (1801, 1802, 1807):
            return False
        raise map_win_error(exc.winerror, f"取消任务 #{job_id}") from exc
    finally:
        if handle is not None:
            try:
                _WIN32PRINT.ClosePrinter(handle)
            except pywintypes.error:  # type: ignore[misc]
                pass


def query_job(printer_name: str, job_id: int) -> JobState:
    """查询任务状态：OpenPrinter -> GetJob(JOB_INFO_1)。

    读取 Status 位、TotalPages、PagesPrinted、pStatus 文本。
    平台不满足时抛 PrintError。
    """
    _require_win32()
    handle = None
    try:
        handle = _WIN32PRINT.OpenPrinter(printer_name)
        info = _WIN32PRINT.GetJob(handle, job_id, 1)
        status = int(info.get("Status") or 0)
        pstatus_text = info.get("pStatus") or ""

        labels = []
        if status & _JOB_STATUS_PAUSED:
            labels.append("已暂停")
        if status & _JOB_STATUS_ERROR:
            labels.append("出错")
        if status & _JOB_STATUS_DELETING:
            labels.append("正在删除")
        if status & _JOB_STATUS_SPOOLING:
            labels.append("正在假脱机")
        if status & _JOB_STATUS_PRINTING:
            labels.append("打印中")
        status_text = pstatus_text or "，".join(labels) or "排队中"
        is_finished = bool(status & (_JOB_STATUS_PRINTED | _JOB_STATUS_DELETED))
        return JobState(
            job_id=job_id,
            status_code=status,
            status_text=status_text,
            pages_printed=info.get("PagesPrinted"),
            is_finished=is_finished,
        )
    except pywintypes.error as exc:  # type: ignore[misc]
        if exc.winerror in (1801, 1802):
            return JobState(job_id=job_id, status_code=exc.winerror,
                            status_text="找不到该打印任务",
                            error_code=PrintError.JOB_NOT_FOUND)
        raise map_win_error(exc.winerror, f"查询任务 #{job_id}") from exc
    finally:
        if handle is not None:
            try:
                _WIN32PRINT.ClosePrinter(handle)
            except pywintypes.error:  # type: ignore[misc]
                pass
