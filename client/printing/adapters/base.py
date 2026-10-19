"""适配器统一接口（契约）。

所有打印后端（Windows 队列、ZPL/TSPL/CPCL 专用协议）都实现本接口。
调用方（UI / 服务层）只依赖这里的方法与 :mod:`client.printing.job` 的数据结构，
不关心具体后端实现。

统一机制（各适配器必须遵守）：
- ``validate_job``：所有校验（尺寸、静区、模块宽度、DPI、能力匹配）在此完成；
  返回 error 级 issue 时禁止提交。
- ``submit``：返回 :class:`PrintResult`；通用路径只能声明 QUEUED，
  不得声称物理打印成功。
- ``cancel`` / ``query_job``：任务取消与状态查询。
- 错误一律抛/包装为 :class:`PrintError`。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..capabilities import PrinterCapabilities
from ..job import (
    JobState, LabelJobSpec, PrintResult, ValidationIssue,
)


class BasePrinterAdapter(ABC):
    """打印机适配器抽象基类。"""

    def __init__(self, capabilities: PrinterCapabilities):
        self._capabilities = capabilities

    # ---- 只读属性 ----
    @property
    def capabilities(self) -> PrinterCapabilities:
        return self._capabilities

    @property
    def printer_name(self) -> str:
        return self._capabilities.printer_name

    @property
    def verified(self) -> bool:
        """是否经真实设备测试。未实测的适配器/设备一律 False。"""
        return self._capabilities.verified_with_hardware

    # ---- 校验 ----
    @abstractmethod
    def validate_job(self, job: LabelJobSpec) -> list[ValidationIssue]:
        """统一校验任务（尺寸/静区/模块宽度/DPI/能力）。error 级阻止提交。"""

    # ---- 构建 ----
    def build_payload(self, job: LabelJobSpec) -> bytes | None:
        """生成发送给设备的负载（专用协议指令）。通用路径返回 None。"""
        return None

    # ---- 执行 ----
    @abstractmethod
    def submit(self, job: LabelJobSpec) -> PrintResult:
        """提交任务。通用队列路径返回 QUEUED（≠物理打印成功）。"""

    @abstractmethod
    def cancel(self, job_id: int | str) -> bool:
        """取消任务，成功返回 True。"""

    @abstractmethod
    def query_job(self, job_id: int | str) -> JobState:
        """查询任务状态。"""

    def close(self) -> None:
        """释放资源（连接句柄等）。幂等。"""
