"""TSPL / TSPL2 专用协议适配器（TSC、立象 Argox 等兼容指令集）。

协议规范来源：TSC Auto ID, *TSPL2 Programming Manual*
（官方文档：https://www.tscprinters.com/EN/support/download ，
公共镜像 https://www.tscprinters.com/cn/cn/service/software ）；
立象 CP 系列兼容 TSPL 子集，见 Argox OS 系列编程手册。

**实测状态（诚信声明）**：按公开规范实现，**未经真实设备实测**，
``verified_with_hardware=False``；实测流程见 docs/PRINTER_TEST_REPORT.md。
支持 DPI：203 / 300 / 600（常见 TSC 机型）。

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
    collect_common_issues, effective_dpi, effective_module_mm,
    has_error, mm_to_dots,
)
from .base import BasePrinterAdapter
from .transports import TransportChannel, build_channel

#: TSPL 支持的条码命令映射（命令名, 参数字符串生成器由 build_payload 处理）
_BARCODE_COMMANDS = {
    "CODE128": "CODE128",
    "EAN13": "EAN13",
    "EAN-13": "EAN13",
}


class TSPLAdapter(BasePrinterAdapter):
    """TSPL2 指令构建与发送。未实测。"""

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
        """生成 TSPL2 指令（SIZE/GAP/DENSITY/DIRECTION/REFERENCE/CLS/
        BARCODE/PRINT/EOP），坐标单位为 dots。"""
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
        gap_dots = mm_to_dots(job.label_gap_mm, dpi)
        # REFERENCE 偏移：负值 TSPL 按反向原点处理，原样下发
        ox = mm_to_dots(job.offset_x_mm, dpi)
        oy = mm_to_dots(job.offset_y_mm, dpi)
        bar_h = mm_to_dots(bar_height_mm, dpi)
        fx = mm_to_dots(origin_x_mm, dpi)
        fy = mm_to_dots(origin_y_mm, dpi)

        data = str(barcode_data or "")
        cmd = _BARCODE_COMMANDS.get(barcode_type.upper())
        if cmd is None:
            raise PrintError(
                PrintError.INVALID_JOB,
                f"TSPL 适配器不支持的条码类型: {barcode_type!r}",
                resolution="当前支持 CODE128 / EAN13",
            )

        if cmd == "CODE128":
            # CODE128 x,y,"data",height,human_readable,rotation,
            #          narrow,wide,mode,checkdigit
            barcode_line = (
                f'CODE128 {fx},{fy},"{data}",{bar_h},1,0,2,2,48,0'
            )
        else:
            # EAN13 x,y,"data",height,human_readable,rotation,
            #       narrow,wide,checkdigit
            barcode_line = (
                f'EAN13 {fx},{fy},"{data}",{bar_h},1,0,2,2,0'
            )

        lines = [
            f"SIZE {width_dots} dots, {height_dots} dots",
            f"GAP {gap_dots} dots, 0",
            f"DENSITY {dpi}",
            "DIRECTION 1,0",
            f"REFERENCE {ox},{oy}",
            "CLS",
            barcode_line,
            f"PRINT {job.copies}",
            "EOP",
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
                "发送 TSPL 指令到设备失败",
                cause=exc,
            ) from exc
        return PrintResult.queued(
            self.printer_name, None,
            "TSPL 指令已发送至设备（设备无 ACK，按已提交处理，"
            "请查看打印机实际输出）。",
        )

    # ---- 取消 / 查询：raw 通道无任务句柄，明确不支持（与 ZPL 一致） ----
    def cancel(self, job_id: int | str) -> bool:
        """raw 直连通道无队列任务句柄，不支持取消（抛 NotImplementedError）。"""
        raise NotImplementedError(
            "TSPL raw 直连通道无系统队列任务句柄，不支持 cancel()"
        )

    def query_job(self, job_id: int | str) -> JobState:
        """raw 直连通道无 ACK/状态回传，不支持查询（抛 NotImplementedError）。"""
        raise NotImplementedError(
            "TSPL raw 直连通道无 ACK/状态回传，不支持 query_job()"
        )

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
