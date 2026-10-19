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

发送语义：raw 通道无设备 ACK，``submit`` 成功也一律返回 ``QUEUED``，
**绝不声称 PHYSICALLY_PRINTED**。
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

#: ZPL ^FD 字段数据推荐上限（字节）
ZPL_MAX_FIELD_DATA_BYTES = 2048
#: ^BY 模块宽度合法区间（dots）
_ZPL_MODULE_DOTS_MIN, _ZPL_MODULE_DOTS_MAX = 1, 10

#: 支持的条码命令映射（值为 ^ 之后的命令名，指令行统一补 ^）
_BARCODE_COMMANDS = {
    "CODE128": "BCN",
    "EAN13": "B3",
    "EAN-13": "B3",
}


class ZPLAdapter(BasePrinterAdapter):
    """ZPL II 指令构建与发送。未实测。"""

    #: 本适配器声明支持的常见 DPI（能力仍以 capabilities 为准）
    SUPPORTED_DPI = (203, 300, 600)

    def __init__(self, capabilities: PrinterCapabilities,
                 channel: TransportChannel | None = None):
        super().__init__(capabilities)
        # 允许注入通道（测试/集成）；缺省由 build_channel 按 transport 构造
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
        """生成 ^XA…^XZ 的 ZPL II 指令字节。

        参数（协议负载辅助，不属于冻结的 LabelJobSpec）：
        - ``barcode_data``：条码内容；``barcode_type``：CODE128 / EAN13；
        - ``origin_x_mm/origin_y_mm``：条码在标签上的原点（mm，左上）；
        - ``bar_height_mm``：条码条高（mm）。
        """
        issues = self.validate_job(job)
        if has_error(issues):
            first = next(i for i in issues if i.severity == "error")
            raise PrintError(first.code, first.message)

        dpi = effective_dpi(job, self._capabilities)
        assert dpi is not None  # validate_job 已拦截
        paper = job.paper
        assert paper is not None

        width_dots = mm_to_dots(paper.width_mm, dpi)
        height_dots = mm_to_dots(paper.height_mm, dpi)
        # ^LH 原点偏移：负值 ZPL 规范允许（向左/向上微调），原样下发
        ox = mm_to_dots(job.offset_x_mm, dpi)
        oy = mm_to_dots(job.offset_y_mm, dpi)

        module_dots = mm_to_dots(effective_module_mm(job), dpi)
        module_dots = max(_ZPL_MODULE_DOTS_MIN,
                          min(_ZPL_MODULE_DOTS_MAX, module_dots))
        bar_h = mm_to_dots(bar_height_mm, dpi)
        fx = mm_to_dots(origin_x_mm, dpi)
        fy = mm_to_dots(origin_y_mm, dpi)

        data = str(barcode_data or "")
        if len(data.encode("latin-1")) > ZPL_MAX_FIELD_DATA_BYTES:
            raise PrintError(
                PrintError.INVALID_JOB,
                f"^FD 字段数据超过 {ZPL_MAX_FIELD_DATA_BYTES} 字节上限",
                resolution="拆分标签或缩短内容",
            )
        cmd = _BARCODE_COMMANDS.get(barcode_type.upper())
        if cmd is None:
            raise PrintError(
                PrintError.INVALID_JOB,
                f"ZPL 适配器不支持的条码类型: {barcode_type!r}",
                resolution="当前支持 CODE128 / EAN13",
            )
        # ^B3 (EAN13)：人眼可读 Y、校验位 N；^BCN (CODE128)：人眼可读 Y、
        # UCC 校验 N、模式 N
        field_suffix = ",Y,N" if cmd == "B3" else ",Y,Y,N"

        lines = [
            "^XA",
            f"^PW{width_dots}",
            f"^LL{height_dots}",
            f"^LH{ox},{oy}",
            f"^BY{module_dots},2,{bar_h}",
            f"^FO{fx},{fy}^{cmd},{bar_h}{field_suffix}^FD{data}^FS",
            "^MMT",   # 撕纸模式（Tear-off）
            "^XZ",
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
                "发送 ZPL 指令到设备失败",
                cause=exc,
            ) from exc
        # raw 通道无 ACK：一律 QUEUED，不得声称物理打印成功
        return PrintResult.queued(
            self.printer_name, None,
            "ZPL 指令已发送至设备（设备无 ACK，按已提交处理，"
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
