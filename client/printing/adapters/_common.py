"""专用协议适配器共享的纯函数与统一校验规则（内部模块，不对外导出）。

统一校验机制（红线，ZPL / TSPL / CPCL / Universal 全部遵守，不得绕过）：

1. **纸张存在且为正**：``job.paper`` 必须存在且 ``width_mm > 0``、
   ``height_mm > 0``，否则 error ``PAPER_UNSUPPORTED``。
2. **能力匹配**：若 ``capabilities.supported_papers`` 非空，纸张须在容差内
   命中；随后 ``capabilities.can_print_size(paper)`` 必须为 True，
   超出可打印区域 → error ``PAPER_OUT_OF_RANGE``。
3. **份数**：``job.copies >= 1``，否则 error ``INVALID_JOB``。
4. **DPI 必须存在**：优先 ``job.dpi``，回退 ``capabilities.resolution()``，
   两者皆无 → error ``INVALID_JOB``（缺分辨率无法做 mm↔dots 换算）。
5. **模块宽度**：``job.module_mm``（缺省取 GS1 标称 0.33mm）必须落在
   ``[GS1_MIN_MODULE_MM, GS1_MAX_MODULE_MM] = [0.264, 0.66]`` mm，
   越界 → error ``MODULE_WIDTH_OUT_OF_RANGE``。
6. **静区**：GS1 要求两侧各 ≥ 11 个模块宽的静区。
   ``job.quiet_zone_mm`` 显式给出且小于 ``11 × module_mm`` →
   error ``QUIET_ZONE_TOO_SMALL``；未给出时按 11 模块计算，不报错。
7. **偏移不越界**：``offset_x_mm / offset_y_mm`` 换算为 dots 后，
   绝对值不得超过标签对应边长（dots），否则 error ``INVALID_JOB``；
   负偏移按各协议规范原样下发（ZPL ^LH / TSPL REFERENCE / CPCL 原点），
   仅做越界拦截。

所有函数均为纯函数，不执行系统调用，可在 Linux 测试环境直接导入。
"""

from __future__ import annotations

from ..capabilities import PrinterCapabilities
from ..errors import PrintError
from ..job import LabelContent, LabelJobSpec, ValidationIssue
from ..templates import (
    GS1_MAX_MODULE_MM,
    GS1_MIN_MODULE_MM,
    GS1_MODULE_MM,
)
from ..units import dots_to_mm as _units_dots_to_mm
from ..units import mm_to_dots as _units_mm_to_dots

#: GS1 静区模块数（EAN/UPC 两侧各 11 模块）
GS1_QUIET_MODULES = 11

#: 浮点比较容差（mm）
_MM_TOLERANCE = 0.05


def mm_to_dots(mm: float, dpi: int) -> int:
    """毫米换算为打印 dots（四舍五入）。

    数学换算唯一来源为 :func:`client.printing.units.mm_to_dots`，
    这里只把其 ``ValueError`` 翻译为统一的 :class:`PrintError`。
    负输入原样保留符号（偏移修正），调用方负责越界校验。
    """
    try:
        return _units_mm_to_dots(mm, dpi)
    except (ValueError, TypeError):
        raise PrintError(
            PrintError.INVALID_JOB,
            "缺少有效的打印分辨率（DPI），无法进行 mm→dots 换算",
        ) from None


def dots_to_mm(dots: int, dpi: int) -> float:
    """dots 换算回毫米（仅用于展示/校验，不用于指令生成）。"""
    try:
        return _units_dots_to_mm(dots, dpi)
    except (ValueError, TypeError):
        raise PrintError(
            PrintError.INVALID_JOB,
            "缺少有效的打印分辨率（DPI），无法进行 dots→mm 换算",
        ) from None


def effective_dpi(job: LabelJobSpec, caps: PrinterCapabilities) -> int | None:
    """任务 DPI 优先，回退设备能力 DPI；都缺则返回 None。"""
    if job.dpi:
        return int(job.dpi)
    return caps.resolution()


def effective_module_mm(job: LabelJobSpec) -> float:
    """任务模块宽度，缺省回退 GS1 标称 0.33mm。"""
    return job.module_mm if job.module_mm is not None else GS1_MODULE_MM


def collect_common_issues(
    job: LabelJobSpec, caps: PrinterCapabilities
) -> list[ValidationIssue]:
    """执行上述 1–7 条统一校验，返回 issue 列表（含 error/warning）。

    专用协议适配器的 ``validate_job`` 应先调用本函数，再追加协议特有校验。
    """
    issues: list[ValidationIssue] = []

    # 1. 纸张存在且为正
    paper = job.paper
    if paper is None or not paper.is_valid:
        issues.append(
            ValidationIssue.error(
                PrintError.PAPER_UNSUPPORTED,
                "任务缺少合法纸张规格（宽度/高度必须为正数 mm）",
            )
        )
        # 纸张非法时后续几何校验无意义，直接返回
        return issues

    # 2a. 命中已登记纸张规格
    if caps.supported_papers:
        hit = caps.find_paper(paper.width_mm, paper.height_mm)
        if hit is None:
            issues.append(
                ValidationIssue.error(
                    PrintError.PAPER_UNSUPPORTED,
                    f"纸张 {paper.width_mm:.2f}×{paper.height_mm:.2f}mm "
                    f"不在设备支持的规格列表中",
                )
            )

    # 2b. 可打印区域
    if not caps.can_print_size(paper):
        issues.append(
            ValidationIssue.error(
                PrintError.PAPER_OUT_OF_RANGE,
                f"纸张 {paper.width_mm:.2f}×{paper.height_mm:.2f}mm "
                "超出打印机可打印区域",
            )
        )

    # 3. 份数
    if not isinstance(job.copies, int) or job.copies < 1:
        issues.append(
            ValidationIssue.error(
                PrintError.INVALID_JOB,
                f"打印份数必须为 >=1 的整数（当前 {job.copies!r}）",
            )
        )

    # 4. DPI
    dpi = effective_dpi(job, caps)
    if dpi is None or dpi <= 0:
        issues.append(
            ValidationIssue.error(
                PrintError.INVALID_JOB,
                "任务与设备能力均未提供有效 DPI，无法按 1:1 物理尺寸换算",
            )
        )

    # 5. 模块宽度
    module_mm = effective_module_mm(job)
    if not (GS1_MIN_MODULE_MM - _MM_TOLERANCE
            <= module_mm <= GS1_MAX_MODULE_MM + _MM_TOLERANCE):
        issues.append(
            ValidationIssue.error(
                PrintError.MODULE_WIDTH_OUT_OF_RANGE,
                f"模块宽度 {module_mm:.3f}mm 超出可识读范围 "
                f"[{GS1_MIN_MODULE_MM}, {GS1_MAX_MODULE_MM}]mm",
            )
        )

    # 6. 静区（仅当任务显式给出静区且不足时报 error）
    required_quiet_mm = GS1_QUIET_MODULES * module_mm
    if job.quiet_zone_mm is not None:
        if job.quiet_zone_mm < required_quiet_mm - _MM_TOLERANCE:
            issues.append(
                ValidationIssue.error(
                    PrintError.QUIET_ZONE_TOO_SMALL,
                    f"静区 {job.quiet_zone_mm:.2f}mm 不足，"
                    f"GS1 要求两侧各 ≥{required_quiet_mm:.2f}mm"
                    f"（{GS1_QUIET_MODULES} 模块 × {module_mm:.3f}mm）",
                )
            )

    # 7. 偏移不越界（仅在 DPI 有效时换算）
    if dpi:
        w_dots = mm_to_dots(paper.width_mm, dpi)
        h_dots = mm_to_dots(paper.height_mm, dpi)
        ox_dots = mm_to_dots(job.offset_x_mm, dpi)
        oy_dots = mm_to_dots(job.offset_y_mm, dpi)
        if abs(ox_dots) > w_dots or abs(oy_dots) > h_dots:
            issues.append(
                ValidationIssue.error(
                    PrintError.INVALID_JOB,
                    f"偏移 ({job.offset_x_mm:.2f}, {job.offset_y_mm:.2f})mm "
                    f"超出标签 {paper.width_mm:.2f}×{paper.height_mm:.2f}mm 范围",
                )
            )

    return issues


def has_error(issues: list[ValidationIssue]) -> bool:
    """是否存在阻止提交的 error 级 issue。"""
    return any(i.severity == "error" for i in issues)


# ---------------------------------------------------------------------------
# LabelContent 解析与布局共享纯函数（专用协议适配器共用）
# ---------------------------------------------------------------------------

def resolve_label_content(
    job: LabelJobSpec, content: LabelContent | None = None
) -> LabelContent:
    """取生效的标签内容：显式 ``content`` 优先，缺省回退 ``job.label_content``。

    两者皆空 → :class:`PrintError`(INVALID_JOB)，专用协议打印不允许无内容静默退化。
    """
    c = content if content is not None else job.label_content
    if c is None:
        raise PrintError(
            PrintError.INVALID_JOB,
            "缺少 label_content，专用协议打印需要完整标签内容",
        )
    return c


def expand_label_instances(content: LabelContent) -> list[tuple[str, str]]:
    """按 ``template_copies`` 把原始 items 展开为标签实例列表（仅展开一次）。

    每个编码实例 = 一张标签；任务份数（``job.copies``）不在此重复，
    由协议打印数量命令唯一应用。
    """
    repeats = max(1, int(content.template_copies))
    instances: list[tuple[str, str]] = []
    for value, btype in content.items:
        instances.extend([(str(value), str(btype))] * repeats)
    return instances


def resolve_module_mm(job: LabelJobSpec, content: LabelContent | None) -> float:
    """模块宽度：content.module_mm 优先，回退 job.module_mm，再回退 GS1 标称。"""
    if content is not None and content.module_mm is not None:
        return content.module_mm
    if job.module_mm is not None:
        return job.module_mm
    return GS1_MODULE_MM


def estimate_barcode_width_dots(
    barcode_type: str, data: str, module_dots: int
) -> int:
    """估算条码条宽（dots），仅用于静区几何校验，不用于指令生成。

    - EAN13：固定 95 模块（含起始/中间/终止条）；
    - CODE128：每数据字符约 11 模块，另加起始/校验/终止约 35 模块。
    """
    t = (barcode_type or "").upper()
    if t in ("EAN13", "EAN-13"):
        modules = 95
    else:
        modules = 11 * len(str(data)) + 35
    return modules * module_dots


def quiet_zone_issue(
    content: LabelContent,
    module_dots: int,
    label_width_dots: int,
    origin_x_dots: int,
    barcode_width_dots: int,
) -> ValidationIssue | None:
    """左右静区几何校验：两侧各需 ≥ quiet_modules × module_dots。

    左静区 = 条码原点预留；右静区 = 标签宽 − 原点 − 条码宽。
    不足返回 error 级 issue，否则 None。
    """
    required = content.quiet_modules * module_dots
    left = origin_x_dots
    right = label_width_dots - origin_x_dots - barcode_width_dots
    if left < required - 1 or right < required - 1:
        return ValidationIssue.error(
            PrintError.QUIET_ZONE_TOO_SMALL,
            f"条码静区不足：左 {left}dots / 右 {right}dots，"
            f"两侧各需 ≥{required}dots"
            f"（{content.quiet_modules} 模块 × {module_dots}dots）",
        )
    return None


def dpi_support_issue(
    dpi: int | None,
    supported_dpi: tuple[int, ...],
    explicit_job_dpi: int | None,
) -> ValidationIssue | None:
    """设备分辨率不在适配器支持列表且任务未显式指定 dpi → error。

    用户显式指定 ``job.dpi`` 时尊重显式选择（交由设备/用户负责），
    仅在"静默采用设备未知分辨率"时拦截，禁止静默用错 DPI。
    """
    if dpi is None or dpi in supported_dpi:
        return None
    if explicit_job_dpi is not None:
        return None
    return ValidationIssue.error(
        PrintError.INVALID_JOB,
        f"设备分辨率 {dpi}dpi 不在本适配器支持列表 "
        f"{list(supported_dpi)}，且任务未显式指定 dpi；"
        "禁止静默按错误分辨率换算",
    )
