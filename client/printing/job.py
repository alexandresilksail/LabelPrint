"""打印任务规格与结果（全项目统一的任务模型）。

关键诚信约定：
- 通用打印（Windows 队列）提交成功后，``outcome`` 只能为 ``QUEUED``，
  提示语必须是"已提交到打印队列"，**不得**表述为"物理打印成功"。
- 只有设备侧明确回报已打印（如专用协议握手确认）时才允许
  ``PHYSICALLY_PRINTED``；无确认通道时一律 QUEUED。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from .capabilities import (
    JobOrientation, PaperSize, PrintQuality,
)
from .errors import PrintError


@dataclass
class LabelJobSpec:
    """一次标签打印任务的完整规格。pdf_path 与 raw_payload 至少其一。

    - pdf_path     ：矢量 PDF 路径（通用打印路径渲染源）
    - raw_payload  ：专用协议指令字节（ZPL/TSPL/CPCL 适配器构建）
    - paper        ：目标纸张/标签规格（毫米，1:1 输出）
    - offset_x/y   ：水平/垂直偏移修正（毫米，来自校准）
    - label_gap_mm ：标签间隙（毫米，仅对支持间隙传感的设备生效）
    - quiet_zone_mm / module_mm：条码静区与模块宽度，用于缩放/识读校验
    - scale_tolerance_mm：缩放检测容差
    """

    pdf_path: str | None = None
    raw_payload: bytes | None = None
    paper: PaperSize | None = None
    orientation: JobOrientation = JobOrientation.PORTRAIT
    copies: int = 1
    quality: PrintQuality = PrintQuality.NORMAL
    dpi: int | None = None
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    label_gap_mm: float = 0.0
    quiet_zone_mm: float | None = None
    module_mm: float | None = None
    scale_tolerance_mm: float = 0.5
    preview_image_path: str | None = None   # 打印预览渲染图（可选）
    #: 专用协议打印的内容模型（ZPL/TSPL/CPCL 适配器据此构建完整指令）
    label_content: "LabelContent | None" = None
    #: 用户已确认的纸张转换许可：仅当 UI 明确提示并获得确认后置 True，
    #: 允许 PDF 以自身实际尺寸（而非目标纸张）输出；否则尺寸不一致直接报错
    allow_paper_conversion: bool = False

    def has_content(self) -> bool:
        return bool(self.pdf_path) or bool(self.raw_payload) or \
            bool(self.label_content)

    def describe(self) -> str:
        if self.paper:
            return (f"{self.paper.name} {self.paper.width_mm:.1f}x"
                    f"{self.paper.height_mm:.1f}mm ×{self.copies}份")
        return f"×{self.copies}份"


@dataclass
class LabelContent:
    """专用协议打印的完整标签内容模型（与 PDF 路径共用同一份数据语义）。

    - ``items``：原始编码列表 [(值, 条码类型)]，**未按模板份数展开**；
      展开只由协议适配器执行一次（``template_copies``）。
    - 偏移/静区/模块宽度来自校准配置，必须实际进入指令（^LH/^BY/GAP/REFERENCE）。
    - ``job_copies`` 不在此重复：任务份数统一取自 ``LabelJobSpec.copies``。
    """

    items: list[tuple[str, str]] = field(default_factory=list)
    template_copies: int = 1
    label_width_mm: float = 40.0
    label_height_mm: float = 25.0
    paper_width_mm: float = 40.0
    paper_height_mm: float = 25.0
    barcode_height_mm: float = 15.0
    show_text: bool = True
    offset_x_mm: float = 0.0
    offset_y_mm: float = 0.0
    module_mm: float | None = None
    quiet_modules: int = 11
    label_gap_mm: float = 0.0


class JobOutcome(str, Enum):
    QUEUED = "queued"                      # 已提交到 Windows 打印队列
    SENT = "sent"                          # 直接通道（TCP 等）已发送，无设备确认
    PHYSICALLY_PRINTED = "physically_printed"  # 设备明确回报已打印
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PrintResult:
    """打印提交结果。message 必须如实描述：
    QUEUED →"已提交到打印队列（任务 #N）"，并提示不代表物理打印完成。"""

    outcome: JobOutcome
    printer_name: str
    message: str
    job_id: int | None = None
    error: PrintError | None = None

    @property
    def succeeded(self) -> bool:
        return self.outcome in (
            JobOutcome.QUEUED, JobOutcome.SENT, JobOutcome.PHYSICALLY_PRINTED)

    @classmethod
    def queued(cls, printer_name: str, job_id: int | None,
               message: str | None = None) -> "PrintResult":
        m = message or (f"已提交到打印队列：{printer_name}"
                        + (f"（任务 #{job_id}）" if job_id else "")
                        + "。提交成功不代表物理打印完成，请查看打印机输出。")
        return cls(outcome=JobOutcome.QUEUED, printer_name=printer_name,
                   message=m, job_id=job_id)

    @classmethod
    def sent(cls, printer_name: str, message: str | None = None) -> "PrintResult":
        """直接通道（TCP 9100 等）已发送到设备。无设备确认，
        绝不表述为物理打印成功。"""
        m = message or (
            f"指令已发送至设备：{printer_name}（设备无确认回执，"
            "不代表物理打印完成，请查看打印机实际输出）。")
        return cls(outcome=JobOutcome.SENT, printer_name=printer_name, message=m)

    @classmethod
    def failed(cls, printer_name: str, error: PrintError) -> "PrintResult":
        return cls(outcome=JobOutcome.FAILED, printer_name=printer_name,
                   message=str(error), error=error)

    @classmethod
    def cancelled(cls, printer_name: str, job_id: int | None) -> "PrintResult":
        return cls(outcome=JobOutcome.CANCELLED, printer_name=printer_name,
                   message=f"打印任务已取消（任务 #{job_id}）", job_id=job_id)


@dataclass(frozen=True)
class ValidationIssue:
    """适配器统一校验输出。severity: error 阻止提交，warning 允许但提示。"""

    severity: str            # "error" | "warning"
    code: str                # 对应 PrintError 错误码或自定义校验码
    message: str

    @classmethod
    def error(cls, code: str, message: str) -> "ValidationIssue":
        return cls("error", code, message)

    @classmethod
    def warning(cls, code: str, message: str) -> "ValidationIssue":
        return cls("warning", code, message)


@dataclass
class JobState:
    """队列任务查询结果。"""

    job_id: int | None
    status_code: int | None = None        # win32print JOB_STATUS_*
    status_text: str = ""                 # 可读状态（排队/打印中/已完成/出错…）
    pages_printed: int | None = None
    is_finished: bool = False
    error_code: str | None = None         # 对应 PrintError 错误码


@dataclass
class ScaleReport:
    """PDF 尺寸/缩放检测结果。"""

    expected_width_mm: float
    expected_height_mm: float
    actual_width_mm: float
    actual_height_mm: float
    tolerance_mm: float = 0.5
    ok: bool = True
    warnings: list[str] = field(default_factory=list)

    @property
    def deviation_x_mm(self) -> float:
        return self.actual_width_mm - self.expected_width_mm

    @property
    def deviation_y_mm(self) -> float:
        return self.actual_height_mm - self.expected_height_mm
