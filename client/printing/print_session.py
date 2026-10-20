"""打印任务会话管理：并发防护、任务句柄、临时文件生命周期。

解决的问题（缺陷清单第 5 项）：
- 重复点击打印 / 并发提交：同一打印机 ``begin_submit`` 忙锁，防并发；
- 切换打印机 / 取消错任务：每台打印机独立 ``ActiveJob``（正确的队列名 + 任务 ID）；
- 临时 PDF 生命周期：登记后由 ``cleanup`` 延迟清理（保留最近 K 个），
  **绝不在提交完成前删除**（系统队列可能仍在读取文件）；
- 异常退出/关闭页面：``cleanup_all`` 一次性清理。

纯逻辑模块（无 Qt / pywin32 依赖），Linux 可直接测试。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import PrintError
from .job import JobState


@dataclass
class ActiveJob:
    """当前活跃打印任务句柄。"""

    printer_name: str
    adapter: Any = None            # BasePrinterAdapter 实例（惰性类型）
    job_id: int | None = None
    pdf_path: str | None = None    # 已登记的临时 PDF（生命周期由会话管理）
    submitted_at: float = field(default_factory=time.time)


class PrintJobSession:
    """每台打印机的任务会话。"""

    def __init__(self, keep_recent: int = 2):
        self._busy: set[str] = set()
        self._active: dict[str, ActiveJob] = {}
        self._temps: list[tuple[str, float]] = []
        self._keep_recent = max(1, int(keep_recent))

    # ---- 并发防护 ----
    def begin_submit(self, printer_name: str) -> None:
        """提交前置锁。同一打印机已有任务在提交/未完成时拒绝并发。"""
        if printer_name in self._busy:
            raise PrintError(
                PrintError.DEVICE_BUSY,
                "该打印机已有打印任务正在处理中",
                resolution="等待当前任务完成后再提交，或先取消任务",
            )
        self._busy.add(printer_name)

    def end_submit(self, printer_name: str) -> None:
        self._busy.discard(printer_name)

    def is_busy(self, printer_name: str) -> bool:
        return printer_name in self._busy

    # ---- 临时文件 ----
    def register_temp(self, path: str) -> None:
        if path:
            self._temps.append((path, time.time()))

    def cleanup(self) -> None:
        """提交完成后调用：保留最近 keep_recent 个临时文件（系统队列
        可能仍在读取），其余安全删除。幂等。"""
        if len(self._temps) <= self._keep_recent:
            return
        keep = self._temps[-self._keep_recent:]
        for path, _ in self._temps[:-self._keep_recent]:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temps = keep

    def cleanup_all(self) -> None:
        """页面关闭/异常退出时调用：清理全部临时文件与任务句柄。"""
        for path, _ in self._temps:
            try:
                os.remove(path)
            except OSError:
                pass
        self._temps.clear()
        self._active.clear()
        self._busy.clear()

    # ---- 任务句柄 ----
    def set_active(self, printer_name: str, adapter: Any, job_id: int | None,
                   pdf_path: str | None = None) -> None:
        self._active[printer_name] = ActiveJob(
            printer_name=printer_name, adapter=adapter, job_id=job_id,
            pdf_path=pdf_path)

    def get_active(self, printer_name: str) -> ActiveJob | None:
        return self._active.get(printer_name)

    def clear_active(self, printer_name: str) -> None:
        self._active.pop(printer_name, None)

    def cancel_active(self, printer_name: str) -> bool:
        """取消当前活跃任务。

        - 无活跃任务/无句柄 → :class:`PrintError`(JOB_NOT_FOUND)；
        - 适配器 ``cancel`` 返回 False（队列拒绝/任务已消失）→
          :class:`PrintError`(SPOOL_FAILED)；
        - 专用直连通道无队列句柄（适配器抛 NotImplementedError）→
          :class:`PrintError`(TRANSPORT_NOT_IMPLEMENTED)；
        - 取消成功才清除 active 句柄。
        """
        job = self._active.get(printer_name)
        if job is None or job.adapter is None or job.job_id is None:
            raise PrintError(
                PrintError.JOB_NOT_FOUND,
                f"打印机 {printer_name} 当前没有可取消的任务",
                resolution="若任务已完成或队列已清空，请刷新后重试",
            )
        try:
            ok = bool(job.adapter.cancel(job.job_id))
        except NotImplementedError:
            raise PrintError(
                PrintError.TRANSPORT_NOT_IMPLEMENTED,
                "专用直连通道无系统队列任务句柄，不支持任务取消",
                resolution="请在设备端停止打印，或改用 Windows 厂商队列打印",
            ) from None
        if not ok:
            raise PrintError(
                PrintError.SPOOL_FAILED,
                f"队列拒绝取消任务 #{job.job_id}（可能已完成或不存在）",
                resolution="请打开系统打印队列核对任务状态",
            )
        self.clear_active(printer_name)
        return ok

    def query_active(self, printer_name: str) -> JobState:
        """查询当前活跃任务状态。

        - 无活跃任务 → JOB_NOT_FOUND；
        - 专用直连通道无状态回传（NotImplementedError）→
          TRANSPORT_NOT_IMPLEMENTED。
        """
        job = self._active.get(printer_name)
        if job is None or job.adapter is None or job.job_id is None:
            raise PrintError(
                PrintError.JOB_NOT_FOUND,
                f"打印机 {printer_name} 当前没有可查询的任务",
            )
        try:
            return job.adapter.query_job(job.job_id)
        except NotImplementedError:
            raise PrintError(
                PrintError.TRANSPORT_NOT_IMPLEMENTED,
                "专用直连通道无任务状态回传，不支持状态查询",
            ) from None

    def active_printers(self) -> list[str]:
        return list(self._active.keys())
