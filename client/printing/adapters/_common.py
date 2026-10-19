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
from ..job import LabelJobSpec, ValidationIssue
from ..templates import (
    GS1_MAX_MODULE_MM,
    GS1_MIN_MODULE_MM,
    GS1_MODULE_MM,
)

#: GS1 静区模块数（EAN/UPC 两侧各 11 模块）
GS1_QUIET_MODULES = 11

#: 浮点比较容差（mm）
_MM_TOLERANCE = 0.05


def mm_to_dots(mm: float, dpi: int) -> int:
    """毫米换算为打印 dots（四舍五入）。

    1 inch = 25.4 mm；dots = mm × dpi / 25.4。
    负输入原样保留符号（偏移修正），调用方负责越界校验。
    """
    if dpi is None or dpi <= 0:
        raise PrintError(
            PrintError.INVALID_JOB,
            "缺少有效的打印分辨率（DPI），无法进行 mm→dots 换算",
        )
    return round(mm * dpi / 25.4)


def dots_to_mm(dots: int, dpi: int) -> float:
    """dots 换算回毫米（仅用于展示/校验，不用于指令生成）。"""
    if dpi is None or dpi <= 0:
        raise PrintError(
            PrintError.INVALID_JOB,
            "缺少有效的打印分辨率（DPI），无法进行 dots→mm 换算",
        )
    return dots * 25.4 / dpi


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
