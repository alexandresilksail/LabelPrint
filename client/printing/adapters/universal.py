"""通用 Windows 打印适配器（驱动 + 系统打印队列主路径，不产生 raw payload）。

**实测状态（诚信声明）**：本适配器按公开接口约定实现，**未经真实设备实测**，
``verified_with_hardware=False``；实测流程见 docs/PRINTER_TEST_REPORT.md。

后端（A 切片，签名冻结，全部方法内惰性 import，本切片不依赖其存在）：
- ``client.printing.qt_print.print_pdf_via_qt(printer_name, pdf_path, spec)
  -> PrintResult``：PDF 经 Qt 打印对话框/驱动渲染；
- ``client.printing.windows_print.spool_raw_data(printer_name, data, doc_name)
  -> PrintResult``：raw 字节走系统队列；
- ``client.printing.pdf_render.detect_scale_mismatch(path, paper,
  tolerance_mm) -> ScaleReport``：PDF 尺寸/缩放预检。

统一校验机制与 :mod:`client.printing.adapters._common` 登记规则一致；
PDF 缩放偏差只产生 ``SCALE_MISMATCH`` **warning**（不阻止提交，UI 警告）。
通用队列路径提交成功只能声明 QUEUED，不得声称物理打印成功。
"""

from __future__ import annotations

from ..capabilities import PrinterCapabilities
from ..errors import PrintError
from ..job import (
    JobState, LabelJobSpec, PrintResult, ValidationIssue,
)
from ._common import collect_common_issues, has_error
from .base import BasePrinterAdapter
from .transports import is_windows


def _choose_backend(job: LabelJobSpec) -> str:
    """纯函数：按任务内容选择后端，返回 ``pdf_qt`` / ``raw_spool``。

    - ``pdf_path`` 非空 → ``pdf_qt``（Qt 渲染 PDF）；
    - 仅 ``raw_payload`` 非空 → ``raw_spool``（系统队列 raw）；
    - 两者皆空 → 抛 ``INVALID_JOB``。
    """
    if job.pdf_path:
        return "pdf_qt"
    if job.raw_payload:
        return "raw_spool"
    raise PrintError(
        PrintError.INVALID_JOB,
        "任务缺少 pdf_path 与 raw_payload，无可提交内容",
    )


class UniversalWindowsAdapter(BasePrinterAdapter):
    """通用 Windows 队列适配器。未实测。"""

    def __init__(self, capabilities: PrinterCapabilities,
                 scale_checker=None):
        """``scale_checker`` 为可注入的缩放检查函数（测试/集成用），
        缺省在 validate_job 内惰性 import ``pdf_render.detect_scale_mismatch``。
        """
        super().__init__(capabilities)
        self._scale_checker = scale_checker

    # ---- 校验（统一机制 + PDF 缩放 warning） ----
    def validate_job(self, job: LabelJobSpec) -> list[ValidationIssue]:
        issues = collect_common_issues(job, self._capabilities)

        # PDF 缩放预检：偏差超容差 → SCALE_MISMATCH warning（不阻止提交）
        if job.pdf_path:
            checker = self._scale_checker
            if checker is None:
                try:
                    from ..pdf_render import detect_scale_mismatch
                except ImportError:
                    # A 切片未集成：跳过，集成阶段重跑（不用 mock 冒充成功）
                    detect_scale_mismatch = None  # type: ignore[assignment]
                checker = detect_scale_mismatch
            if checker is not None:
                try:
                    report = checker(
                        job.pdf_path, job.paper, job.scale_tolerance_mm)
                except FileNotFoundError:
                    # 干跑/预览阶段 PDF 尚未落盘：跳过缩放检查，不阻断提交
                    report = None
                if report is not None and not report.ok:
                    issues.append(
                        ValidationIssue.warning(
                            PrintError.SCALE_MISMATCH,
                            "检测到非预期缩放，标签可能无法按 1:1 打印："
                            + "；".join(report.warnings)
                            or f"尺寸偏差 "
                               f"({report.deviation_x_mm:+.2f},"
                               f"{report.deviation_y_mm:+.2f})mm",
                        )
                    )
        return issues

    # ---- 构建：通用路径不产生 raw payload ----
    def build_payload(self, job: LabelJobSpec) -> bytes | None:
        return None

    # ---- 执行 ----
    def submit(self, job: LabelJobSpec) -> PrintResult:
        issues = self.validate_job(job)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        backend = _choose_backend(job)

        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "通用打印队列仅支持 Windows 平台",
                resolution="非 Windows 环境无系统打印队列与 Qt 打印后端",
            )

        if backend == "pdf_qt":
            try:
                from ..qt_print import print_pdf_via_qt
            except ImportError as exc:
                raise PrintError(
                    PrintError.QT_BACKEND_UNAVAILABLE,
                    "缺少 Qt 打印后端（qt_print.print_pdf_via_qt）",
                    resolution="A 切片在 Windows + PySide6 环境提供，集成阶段重跑",
                    cause=exc,
                ) from exc
            return print_pdf_via_qt(self.printer_name, job.pdf_path, job)

        # raw_spool
        try:
            from ..windows_print import spool_raw_data
        except ImportError as exc:
            raise PrintError(
                PrintError.WIN32PRINT_UNAVAILABLE,
                "缺少 Windows 队列后端（windows_print.spool_raw_data）",
                resolution="A 切片在 Windows + pywin32 环境提供，集成阶段重跑",
                cause=exc,
            ) from exc
        return spool_raw_data(self.printer_name, job.raw_payload, "Label")

    # ---- 取消 / 查询：委托 Windows 队列（惰性 import） ----
    def cancel(self, job_id: int | str) -> bool:
        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "任务取消仅支持 Windows 队列",
            )
        try:
            from .. import windows_print
        except ImportError as exc:
            raise PrintError(
                PrintError.WIN32PRINT_UNAVAILABLE,
                "缺少 Windows 队列后端（windows_print）",
                cause=exc,
            ) from exc
        fn = getattr(windows_print, "cancel_job", None)
        if fn is None:
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "Windows 后端未提供 cancel_job，集成阶段补全",
            )
        return bool(fn(self.printer_name, job_id))

    def query_job(self, job_id: int | str) -> JobState:
        if not is_windows():
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "任务状态查询仅支持 Windows 队列",
            )
        try:
            from .. import windows_print
        except ImportError as exc:
            raise PrintError(
                PrintError.WIN32PRINT_UNAVAILABLE,
                "缺少 Windows 队列后端（windows_print）",
                cause=exc,
            ) from exc
        fn = getattr(windows_print, "query_job", None)
        if fn is None:
            raise PrintError(
                PrintError.UNSUPPORTED_PLATFORM,
                "Windows 后端未提供 query_job，集成阶段补全",
            )
        return fn(self.printer_name, job_id)
