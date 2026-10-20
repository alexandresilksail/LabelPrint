"""ZPL II 专用协议适配器（Zebra 及兼容打印机）。

协议规范来源：Zebra Technologies, *ZPL II Programming Guide*
（官方文档：https://support.zebra.com/cpws/docs/zpl/zpl_manual.pdf ，
公共镜像 https://www.zebra.com/content/dam/zebra_new_ia/en-us/manuals/printers/common/programming/zpl-zbi2-pm-en.pdf）。

**实测状态（诚信声明）**：按公开规范实现，**未经真实设备实测**，
``verified_with_hardware=False``；实测流程见 docs/PRINTER_TEST_REPORT.md。
支持 DPI：203 / 300 / 600（常见机型，如 ZD420/ZD620 系列）；
152 DPI 等特殊分辨率不在本切片保证范围。

统一校验机制与 :mod:`client.printing.adapters._common` 中登记的规则完全一致
（尺寸/能力匹配/份数/DPI/模块宽度/静区/偏移越界），本适配器不绕过。

内容模型（冻结接口）：
``build_payload(job, *, content=None)``，``content`` 缺省取 ``job.label_content``；
两者皆空抛 ``INVALID_JOB``。``items`` 按 ``template_copies`` 展开一次，
每个编码实例生成一个 ``^XA…^XZ`` 块；任务份数 ``job.copies`` 由每块
``^PQ`` 唯一应用，不在别处重复。

发送语义：raw 通道无设备 ACK，TCP 9100 发送成功只返回 ``SENT``
（指令已发送、无设备确认、不代表物理打印完成），**绝不声称
PHYSICALLY_PRINTED**；spool 通道返回 ``QUEUED``。
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

#: ZPL ^FD 字段数据推荐上限（字节）
ZPL_MAX_FIELD_DATA_BYTES = 2048
#: ^BY 模块宽度合法区间（dots）
_ZPL_MODULE_DOTS_MIN, _ZPL_MODULE_DOTS_MAX = 1, 10
#: 条码顶部默认边距（mm，^FO Y 起点）
_DEFAULT_TOP_MARGIN_MM = 3.0

#: 支持的条码命令映射（值为 ^ 之后的命令名，指令行统一补 ^）
_BARCODE_COMMANDS = {
    "CODE128": "BCN",
    "EAN13": "B3",
    "EAN-13": "B3",
}
_SUPPORTED_BARCODES = ("CODE128", "EAN13")


class ZPLAdapter(BasePrinterAdapter):
    """ZPL II 指令构建与发送。未实测。"""

    #: 本适配器声明支持的常见 DPI（能力仍以 capabilities 为准）
    SUPPORTED_DPI = (203, 300, 600)

    def __init__(self, capabilities: PrinterCapabilities,
                 channel: TransportChannel | None = None):
        super().__init__(capabilities)
        # 允许注入通道（测试/集成）；缺省由 build_channel 按 transport 构造
        self._channel = channel

    # ---- 校验（统一机制 + ZPL 特有检查） ----
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

        # 模块宽度（content 优先）越界检查
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

        # 静区几何：左静区 = 原点预留，右静区 = 标签宽 − 原点 − 条码宽
        module_dots = self._clamp_module_dots(
            mm_to_dots(module_mm, dpi))
        origin_x = content.quiet_modules * module_dots
        label_w = mm_to_dots(content.label_width_mm, dpi)
        for value, btype in expand_label_instances(content):
            if btype.upper() not in _BARCODE_COMMANDS:
                continue  # 未知类型在 build_payload 抛 PrintError，此处不估算
            bw = estimate_barcode_width_dots(btype, value, module_dots)
            qz = quiet_zone_issue(content, module_dots, label_w, origin_x, bw)
            if qz is not None:
                issues.append(qz)
                break
        return issues

    @staticmethod
    def _clamp_module_dots(module_dots: int) -> int:
        return max(_ZPL_MODULE_DOTS_MIN,
                   min(_ZPL_MODULE_DOTS_MAX, module_dots))

    # ---- 构建（冻结接口：build_payload(job, *, content=None)） ----
    def build_payload(
        self,
        job: LabelJobSpec,
        *,
        content: LabelContent | None = None,
    ) -> bytes:
        """生成逐实例 ``^XA…^XZ`` 的 ZPL II 指令字节。

        - ``content`` 缺省取 ``job.label_content``，两者皆空抛 INVALID_JOB；
        - ``items`` 按 ``template_copies`` 展开，每实例一个指令块；
        - ``job.copies`` 由每块 ``^PQ`` 唯一应用；
        - 偏移经 ``^LH`` 进入指令；模块宽经 ``^BY``；静区计入 ``^FO`` 原点；
        - ``label_gap_mm`` 是介质传感参数：ZPL 无逐任务 GAP 命令，本切片
          明确视为"设备参数已传递"（由设备介质配置/校准提供），不在指令中伪造。
        """
        c = resolve_label_content(job, content)
        issues = self._issues(job, c)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        dpi = effective_dpi(job, self._capabilities)
        assert dpi is not None  # _issues 已拦截

        width_dots = mm_to_dots(c.label_width_mm, dpi)
        height_dots = mm_to_dots(c.label_height_mm, dpi)
        # ^LH 原点偏移：负值 ZPL 规范允许（向左/向上微调），原样下发
        ox = mm_to_dots(c.offset_x_mm, dpi)
        oy = mm_to_dots(c.offset_y_mm, dpi)

        module_dots = self._clamp_module_dots(
            mm_to_dots(resolve_module_mm(job, c), dpi))
        bar_h = mm_to_dots(c.barcode_height_mm, dpi)
        # 左静区预留为条码原点；顶部默认边距
        fx = c.quiet_modules * module_dots
        fy = mm_to_dots(_DEFAULT_TOP_MARGIN_MM, dpi)

        blocks: list[str] = []
        for value, btype in expand_label_instances(c):
            cmd = _BARCODE_COMMANDS.get(btype.upper())
            if cmd is None:
                raise PrintError(
                    PrintError.INVALID_JOB,
                    f"ZPL 适配器不支持的条码类型: {btype!r}",
                    resolution=f"当前支持 {' / '.join(_SUPPORTED_BARCODES)}",
                )
            data = str(value)
            if len(data.encode("latin-1")) > ZPL_MAX_FIELD_DATA_BYTES:
                raise PrintError(
                    PrintError.INVALID_JOB,
                    f"^FD 字段数据超过 {ZPL_MAX_FIELD_DATA_BYTES} 字节上限",
                    resolution="拆分标签或缩短内容",
                )
            hr = "Y" if c.show_text else "N"
            # ^B3 (EAN13)：人眼可读 Y/N、校验位 N；^BCN (CODE128)：
            # 人眼可读 Y/N、UCC 校验 Y、模式 N
            suffix = f",{hr},N" if cmd == "B3" else f",{hr},Y,N"
            blocks.append(
                "\r\n".join([
                    "^XA",
                    f"^PW{width_dots}",
                    f"^LL{height_dots}",
                    f"^LH{ox},{oy}",
                    f"^BY{module_dots},2,{bar_h}",
                    f"^FO{fx},{fy}^{cmd},{bar_h}{suffix}^FD{data}^FS",
                    "^MMT",
                    f"^PQ{job.copies}",
                    "^XZ",
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
                "发送 ZPL 指令到设备失败",
                cause=exc,
            ) from exc

        # 按通道诚实判定：TCP 9100 已发送 = SENT（无 ACK，≠物理打印成功）；
        # spool 队列 = QUEUED。
        if channel.kind == "tcp":
            return PrintResult.sent(
                self.printer_name,
                "ZPL 指令已发送至设备（设备无确认回执，"
                "不代表物理打印完成，请查看打印机实际输出）。",
            )
        return PrintResult.queued(
            self.printer_name, None,
            "ZPL 指令已提交到队列（提交成功不代表物理打印完成，"
            "请查看打印机实际输出）。",
        )

    # ---- 取消 / 查询：raw 通道无任务句柄，明确不支持 ----
    def cancel(self, job_id: int | str) -> bool:
        """raw 直连通道无队列任务句柄，不支持取消。

        语义：始终抛出 :class:`NotImplementedError`，调用方应捕获并提示
        "专用直连设备不支持任务取消"。
        """
        raise NotImplementedError(
            "ZPL raw 直连通道无系统队列任务句柄，不支持 cancel()"
        )

    def query_job(self, job_id: int | str) -> JobState:
        """raw 直连通道不回传任务状态，不支持查询。

        语义：始终抛出 :class:`NotImplementedError`。
        """
        raise NotImplementedError(
            "ZPL raw 直连通道无 ACK/状态回传，不支持 query_job()"
        )

    def close(self) -> None:
        if self._channel is not None:
            self._channel.close()
