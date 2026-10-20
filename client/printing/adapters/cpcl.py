"""CPCL 专用协议适配器（Honeywell/Intermec、Star 等兼容指令集）。

协议规范来源：Honeywell/Intermec, *CPCL Programming Manual*
（官方文档：https://honeywell.com/ 服务支持页，公共镜像
https://www.intermec.com/ 开发者文档库）；Star 热敏机兼容 CPCL 子集，
见 Star Command Mode 手册。

**实测状态（诚信声明）**：按公开规范实现，**未经真实设备实测**，
``verified_with_hardware=False``；实测流程见 docs/PRINTER_TEST_REPORT.md。
支持 DPI：203 / 300 / 600（常见 Honeywell/Star 机型）。

统一校验机制与 :mod:`client.printing.adapters._common` 登记规则一致，
本适配器不绕过。

内容模型（冻结接口）：``build_payload(job, *, content=None)``，
``items`` 按 ``template_copies`` 展开，每实例一个
``! 0 / PAGE-WIDTH / BARCODE / FORM / PRINT n`` 块；
``job.copies`` 由每实例 ``PRINT n`` 唯一应用。CPCL 无原点偏移命令，
偏移直接平移条码绝对坐标。

发送语义：TCP 9100 发送成功只返回 ``SENT``（无设备确认，≠物理打印成功）；
spool 队列返回 ``QUEUED``。
"""

from __future__ import annotations

from ..capabilities import PrinterCapabilities
from ..errors import PrintError
from ..job import (
    JobState, LabelContent, LabelJobSpec, PrintResult, ValidationIssue,
)
from ..templates import GS1_MAX_MODULE_MM, GS1_MIN_MODULE_MM
from ._common import (
    collect_common_issues, dpi_support_issue, effective_dpi,
    estimate_barcode_width_dots, expand_label_instances, has_error,
    mm_to_dots, quiet_zone_issue, resolve_label_content, resolve_module_mm,
)
from .base import BasePrinterAdapter
from .transports import TransportChannel, build_channel

#: 条码顶部默认边距（mm，BARCODE Y 起点）
_DEFAULT_TOP_MARGIN_MM = 3.0

#: CPCL 支持的条码类型映射（CPCL BARCODE 命令名）
_BARCODE_COMMANDS = {
    "CODE128": "CODE128",
    "EAN13": "EAN13",
    "EAN-13": "EAN13",
}
_SUPPORTED_BARCODES = ("CODE128", "EAN13")


class CPCLAdapter(BasePrinterAdapter):
    """CPCL 指令构建与发送。未实测。"""

    SUPPORTED_DPI = (203, 300, 600)

    def __init__(self, capabilities: PrinterCapabilities,
                 channel: TransportChannel | None = None):
        super().__init__(capabilities)
        self._channel = channel

    # ---- 校验（统一机制 + CPCL 特有检查） ----
    def validate_job(self, job: LabelJobSpec) -> list[ValidationIssue]:
        return self._issues(job, job.label_content)

    def _issues(
        self, job: LabelJobSpec, content: LabelContent | None
    ) -> list[ValidationIssue]:
        issues = collect_common_issues(job, self._capabilities)

        dpi = effective_dpi(job, self._capabilities)
        dpi_issue = dpi_support_issue(dpi, self.SUPPORTED_DPI, job.dpi)
        if dpi_issue is not None:
            issues.append(dpi_issue)

        if content is None or dpi is None:
            return issues

        module_mm = resolve_module_mm(job, content)
        if not (GS1_MIN_MODULE_MM - 0.05 <= module_mm <= GS1_MAX_MODULE_MM + 0.05):
            issues.append(
                ValidationIssue.error(
                    PrintError.MODULE_WIDTH_OUT_OF_RANGE,
                    f"模块宽度 {module_mm:.3f}mm 超出可识读范围 "
                    f"[{GS1_MIN_MODULE_MM}, {GS1_MAX_MODULE_MM}]mm",
                )
            )

        if content.template_copies < 1:
            issues.append(
                ValidationIssue.error(
                    PrintError.INVALID_JOB,
                    f"template_copies 必须 >=1（当前 {content.template_copies!r}）",
                )
            )

        module_dots = mm_to_dots(module_mm, dpi)
        ox = mm_to_dots(content.offset_x_mm, dpi)
        origin_x = content.quiet_modules * module_dots + ox
        label_w = mm_to_dots(content.label_width_mm, dpi)
        for value, btype in expand_label_instances(content):
            if btype.upper() not in _BARCODE_COMMANDS:
                continue
            bw = estimate_barcode_width_dots(btype, value, module_dots)
            qz = quiet_zone_issue(content, module_dots, label_w, origin_x, bw)
            if qz is not None:
                issues.append(qz)
                break
        return issues

    # ---- 构建（冻结接口：build_payload(job, *, content=None)） ----
    def build_payload(
        self,
        job: LabelJobSpec,
        *,
        content: LabelContent | None = None,
    ) -> bytes:
        """生成逐实例 CPCL 指令块（页头 ! 0 / PAGE-WIDTH / BARCODE / FORM / PRINT）。

        - ``content`` 缺省取 ``job.label_content``，两者皆空抛 INVALID_JOB；
        - CPCL 无原点命令，偏移平移条码绝对坐标；任务份数由 ``PRINT n``。
        """
        c = resolve_label_content(job, content)
        issues = self._issues(job, c)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        dpi = effective_dpi(job, self._capabilities)
        assert dpi is not None

        width_dots = mm_to_dots(c.label_width_mm, dpi)
        height_dots = mm_to_dots(c.label_height_mm, dpi)
        # CPCL 原点偏移：页头之后以绝对坐标定位，偏移平移起点坐标
        ox = mm_to_dots(c.offset_x_mm, dpi)
        oy = mm_to_dots(c.offset_y_mm, dpi)
        bar_h = mm_to_dots(c.barcode_height_mm, dpi)
        module_dots = mm_to_dots(resolve_module_mm(job, c), dpi)
        # 左静区预留为条码原点，再叠加偏移；顶部默认边距叠加纵向偏移
        fx = c.quiet_modules * module_dots + ox
        fy = mm_to_dots(_DEFAULT_TOP_MARGIN_MM, dpi) + oy

        blocks: list[str] = []
        for value, btype in expand_label_instances(c):
            cmd = _BARCODE_COMMANDS.get(btype.upper())
            if cmd is None:
                raise PrintError(
                    PrintError.INVALID_JOB,
                    f"CPCL 适配器不支持的条码类型: {btype!r}",
                    resolution=f"当前支持 {' / '.join(_SUPPORTED_BARCODES)}",
                )
            data = str(value)
            blocks.append(
                "\r\n".join([
                    f"! 0 {dpi} {dpi} {height_dots} 1",
                    f"PAGE-WIDTH {width_dots}",
                    f"BARCODE {cmd} {fx} {fy} {bar_h} {data}",
                    "FORM",
                    f"PRINT {job.copies}",
                ])
            )
        return ("\r\n".join(blocks) + "\r\n").encode("latin-1")

    # ---- 执行 ----
    def submit(self, job: LabelJobSpec) -> PrintResult:
        issues = self.validate_job(job)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        channel = self._channel
        if channel is None:
            channel = build_channel(self._capabilities)

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

        if channel.kind == "tcp":
            return PrintResult.sent(
                self.printer_name,
                "CPCL 指令已发送至设备（设备无确认回执，"
                "不代表物理打印完成，请查看打印机实际输出）。",
            )
        return PrintResult.queued(
            self.printer_name, None,
            "CPCL 指令已提交到队列（提交成功不代表物理打印完成，"
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
