"""打印统一错误分类。

全项目打印相关异常一律使用 :class:`PrintError` 抛出/包装，
UI 层只按 ``code`` 分类展示，禁止各适配器私自发明错误类型。

错误码即契约：新增错误码必须先在此登记。
"""

from __future__ import annotations


class PrintError(Exception):
    """统一打印错误。

    - ``code``       ：机器可读错误码（见 CODES 登记）
    - ``user_message``：直接给用户看的中文提示
    - ``resolution`` ：修复指引（提示下一步操作，可为空）
    - ``cause``      ：原始异常（调试用）
    """

    # ---- 平台/环境 ----
    UNSUPPORTED_PLATFORM = "UNSUPPORTED_PLATFORM"        # 非 Windows 或无可用后端
    QT_BACKEND_UNAVAILABLE = "QT_BACKEND_UNAVAILABLE"   # PySide6 缺失
    WIN32PRINT_UNAVAILABLE = "WIN32PRINT_UNAVAILABLE"   # pywin32 缺失

    # ---- 打印机/队列/驱动 ----
    NO_DEFAULT_PRINTER = "NO_DEFAULT_PRINTER"           # 无默认打印机
    QUEUE_MISSING = "QUEUE_MISSING"                     # 队列不存在
    DRIVER_MISSING = "DRIVER_MISSING"                   # 驱动缺失/未正确安装
    DRIVER_INSTALL_FAILED = "DRIVER_INSTALL_FAILED"     # 驱动安装失败
    DEVICE_OFFLINE = "DEVICE_OFFLINE"                   # 设备离线
    DEVICE_PAUSED = "DEVICE_PAUSED"
    DEVICE_BUSY = "DEVICE_BUSY"
    ACCESS_DENIED = "ACCESS_DENIED"                     # 无权限（常需管理员）

    # ---- 任务/数据 ----
    PAPER_UNSUPPORTED = "PAPER_UNSUPPORTED"             # 纸张规格不受支持
    PAPER_OUT_OF_RANGE = "PAPER_OUT_OF_RANGE"           # 超出可打印区域
    PAPER_SIZE_MISMATCH = "PAPER_SIZE_MISMATCH"         # PDF 页面尺寸与纸张规格不一致
    SCALE_MISMATCH = "SCALE_MISMATCH"                   # 检测到非预期缩放
    QUIET_ZONE_TOO_SMALL = "QUIET_ZONE_TOO_SMALL"       # 条码静区不足
    MODULE_WIDTH_OUT_OF_RANGE = "MODULE_WIDTH_OUT_OF_RANGE"  # 模块宽度越界
    SPOOL_FAILED = "SPOOL_FAILED"                       # 提交到队列失败
    JOB_CANCELLED = "JOB_CANCELLED"                     # 任务被取消
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    INVALID_JOB = "INVALID_JOB"                         # 任务参数非法
    PROTOCOL_UNSUPPORTED = "PROTOCOL_UNSUPPORTED"       # 设备不支持该协议
    TRANSPORT_NOT_IMPLEMENTED = "TRANSPORT_NOT_IMPLEMENTED"  # 传输通道未集成（USB/蓝牙/LPR 等）

    CODES = {
        UNSUPPORTED_PLATFORM: "当前平台/后端不支持该操作",
        QT_BACKEND_UNAVAILABLE: "缺少 PySide6（Qt）后端",
        WIN32PRINT_UNAVAILABLE: "缺少 pywin32（win32print）",
        NO_DEFAULT_PRINTER: "系统中没有默认打印机",
        QUEUE_MISSING: "找不到对应的打印队列",
        DRIVER_MISSING: "该设备的打印机驱动缺失",
        DRIVER_INSTALL_FAILED: "打印机驱动安装失败",
        DEVICE_OFFLINE: "打印机处于离线状态",
        DEVICE_PAUSED: "打印机队列已暂停",
        DEVICE_BUSY: "打印机忙碌",
        ACCESS_DENIED: "没有足够权限执行该操作",
        PAPER_UNSUPPORTED: "所选纸张规格不受该打印机支持",
        PAPER_OUT_OF_RANGE: "标签尺寸超出打印机可打印区域",
        PAPER_SIZE_MISMATCH: "PDF 页面尺寸与打印纸张规格不一致",
        SCALE_MISMATCH: "检测到非预期缩放，标签可能无法按 1:1 打印",
        QUIET_ZONE_TOO_SMALL: "条码静区不足，可能无法稳定识读",
        MODULE_WIDTH_OUT_OF_RANGE: "条码模块宽度超出可识读范围",
        SPOOL_FAILED: "打印任务提交到系统队列失败",
        JOB_CANCELLED: "打印任务已被取消",
        JOB_NOT_FOUND: "找不到该打印任务",
        INVALID_JOB: "打印任务参数非法",
        PROTOCOL_UNSUPPORTED: "该设备不支持所选打印协议",
        TRANSPORT_NOT_IMPLEMENTED: "该传输通道尚未集成（如 USB/蓝牙/LPR 直连）",
    }

    def __init__(self, code: str, user_message: str | None = None,
                 resolution: str = "", *, cause: Exception | None = None):
        if code not in self.CODES:
            raise ValueError(f"未登记的打印错误码: {code!r}（请先在 errors.py 登记）")
        self.code = code
        self.resolution = resolution
        self.cause = cause
        msg = user_message or self.CODES[code]
        if resolution:
            msg = f"{msg}。{resolution}"
        super().__init__(msg)

    @property
    def user_message(self) -> str:
        return str(self)


def wrap_os_error(code: str, exc: OSError, action: str) -> PrintError:
    """把 Windows API 的 OSError 包装为 PrintError（保留错误码供排查）。"""
    detail = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    resolution = f"（Windows 错误 {detail}，操作：{action}）" if detail else \
        f"（操作：{action}）"
    return PrintError(code, resolution=resolution, cause=exc)
