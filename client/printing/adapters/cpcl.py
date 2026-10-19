"""CPCL 专用协议适配器（Honeywell/Intermec、Star 等兼容指令集）。

协议规范来源：Honeywell/Intermec, *CPCL Programming Manual*
（官方文档：https://honeywell.com/ 服务支持页，公共镜像
https://www.intermec.com/ 开发者文档库）；Star 热敏机兼容 CPCL 子集，
见 Star Command Mode 手册。

**实测状态（诚信声明）**：按公开规范实现，**未经真实设备实测**，
``verified_with_hardware=False``；实测流程见 docs/PRINTER_TEST_REPORT.md。
支持 DPI：203 / 300 / 600（常见 Honeywell/Star 机型）。

统一校验机制与 :mod:`client.printing.adapters._common` 登记规则一致，
本适配器不绕过。raw 通道无 ACK，``submit`` 一律 QUEUED。
"""

from __future__ import annotations

from ..capabilities import PrinterCapabilities
from ..errors import PrintError
from ..job import (
    JobState, LabelJobSpec, PrintResult, ValidationIssue,
)
from ._common import (
    collect_common_issues, effective_dpi, has_error, mm_to_dots,
)
from .base import BasePrinterAdapter
from .transports import TransportChannel, build_channel

#: CPCL 支持的条码类型映射（CPCL BARCODE 命令名）
_BARCODE_COMMANDS = {
    "CODE128": "CODE128",
    "EAN13": "EAN13",
    "EAN-13": "EAN13",
}


class CPCLAdapter(BasePrinterAdapter):
    """CPCL 指令构建与发送。未实测。"""

    SUPPORTED_DPI = (203, 300, 600)

    def __init__(self, capabilities: PrinterCapabilities,
                 channel: TransportChannel | None = None):
        super().__init__(capabilities)
        self._channel = channel

    # ---- 校验（统一机制，规则见 _common.collect_common_issues） ----
    def validate_job(self, job: LabelJobSpec) -> list[ValidationIssue]:
        return collect_common_issues(job, self._capabilities)

    # ---- 构建 ----
    def build_payload(
        self,
        job: LabelJobSpec,
        *,
        barcode_data: str | None = None,
        barcode_type: str = "CODE128",
        origin_x_mm: float = 3.0,
        origin_y_mm: float = 3.0,
        bar_height_mm: float = 15.0,
    ) -> bytes:
        """生成 CPCL 指令（页头 ! 0 / PAGE-WIDTH / BARCODE / FORM / PRINT）。"""
        issues = self.validate_job(job)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        dpi = effective_dpi(job, self._capabilities)
        assert dpi is not None
        paper = job.paper
        assert paper is not None

        width_dots = mm_to_dots(paper.width_mm, dpi)
        height_dots = mm_to_dots(paper.height_mm, dpi)
        # CPCL 原点偏移：页头之后以绝对坐标定位，偏移通过起点坐标平移实现
        ox = mm_to_dots(job.offset_x_mm, dpi)
        oy = mm_to_dots(job.offset_y_mm, dpi)
        bar_h = mm_to_dots(bar_height_mm, dpi)
        fx = mm_to_dots(origin_x_mm, dpi) + ox
        fy = mm_to_dots(origin_y_mm, dpi) + oy

        data = str(barcode_data or "")
        cmd = _BARCODE_COMMANDS.get(barcode_type.upper())
        if cmd is None:
            raise PrintError(
                PrintError.INVALID_JOB,
                f"CPCL 适配器不支持的条码类型: {barcode_type!r}",
                resolution="当前支持 CODE128 / EAN13",
            )

        lines = [
            f"! 0 {dpi} {dpi} {height_dots} 1",
            f"PAGE-WIDTH {width_dots}",
            f"BARCODE {cmd} {fx} {fy} {bar_h} {data}",
            "FORM",
            f"PRINT {job.copies}",
        ]
        return ("\r\n".join(lines) + "\r\n").encode("latin-1")

    # ---- 执行 ----
    def submit(self, job: LabelJobSpec) -> PrintResult:
        issues = self.validate_job(job)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        channel = self._channel
        if channel is None:
            try:
                channel = build_channel(self._capabilities)
            except PrintError:
                raise PrintError(
                    PrintError.UNSUPPORTED_PLATFORM,
                    "无可用传输通道（非 Windows 或设备连接方式不受支持）",
                    resolution="网络 9100 通道可跨平台；本机队列/USB/蓝牙需 Windows",
                ) from None

        payload = self.build_payload(job)
        try:
            channel.open()
            channel.write(payload)
            channel.flush()
        except PrintError:
            raise
        except OSError as exc:
            raise PrintError(
                PrintError.SPOOL_FAILED,
                "发送 CPCL 指令到设备失败",
                cause=exc,
            ) from exc
        return PrintResult.queued(
            self.printer_name, None,
            "CPCL 指令已发送至设备（设备无 ACK，按已提交处理，"
            "请查看打印机实际输出）。",
        )

    # ---- 取消 / 查询：raw 通道无任务句柄，明确不支持（与 ZPL/TSPL 一致） ----
    def cancel(self, job_id: int | str) -> bool:
        """raw 直连通道无队列任务句柄，不支持取消（抛 NotImplementedError）。"""
        raise NotImplementedError(
            "CPCL raw 直连通道无系统队列任务句柄，不支持 cancel()"
        )

    def query_job(self, job_id: int | str) -> JobState:
        """raw 直连通道无 ACK/状态回传，不支持查询（抛 NotImplementedError）。"""
        raise NotImplementedError(
            "CPCL raw 直连通道无 ACK/状态回传，不支持 query_job()"
        )

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
