"""缩放风险严格分级（纯函数，无 Qt / 无硬件依赖，Linux 可测）。

核心诚信语义（全模块统一，不得弱化）：

**PDF 页面尺寸正确 ≠ 物理标签输出尺寸正确。**
软件按精确毫米把页面尺寸下发给系统队列，但第三方驱动是否再缩放、
目标设备能否按该物理尺寸走纸，本软件**无法控制也无法代为确认**；
因此：

- 尺寸超容差且未获用户转换许可 → ``block``（阻止提交，禁止静默
  拉伸/压缩）；
- 尺寸超容差但已获用户明确确认转换 → ``warn``（按 PDF 实际尺寸
  1:1 输出，仍提示驱动风险）；
- 尺寸在容差内但设备未声明 ``supports_true_size`` → ``warn``；
- ``supports_true_size`` 未知（caps 缺失 / 属性为 None）→ ``warn``；
- 尺寸匹配且设备明确声明支持 1:1 → ``ok``。

``warn`` 不是“可以无提示继续”：UI 必须弹出列出全部原因的对话框，
提供【取消】与【继续打印】两个选项，由用户显式决定。本模块只做
分级与文案，不接触系统 API，不伪造设备能力。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import units
from .capabilities import PaperSize

# ---- 等级 ----
RISK_OK = "ok"
RISK_WARN = "warn"
RISK_BLOCK = "block"

# ---- 原因码（机器可读，供 UI 分组/测试断言）----
REASON_INVALID_PAPER = "INVALID_PAPER"
REASON_PAPER_SIZE_MISMATCH = "PAPER_SIZE_MISMATCH"
REASON_PAPER_CONVERTED = "PAPER_CONVERTED"
REASON_TRUE_SIZE_NOT_DECLARED = "TRUE_SIZE_NOT_DECLARED"
REASON_TRUE_SIZE_UNKNOWN = "TRUE_SIZE_UNKNOWN"

#: 诚信固定措辞：页面尺寸正确不代表/不等于物理输出尺寸正确
HONESTY_REMINDER = (
    "PDF 页面尺寸正确不代表物理标签输出尺寸正确，"
    "页面尺寸正确也不等于物理标签输出尺寸正确；"
    "第三方驱动缩放不受本软件控制，请先以测试页复核实际输出"
)

#: 原因码 → 给用户看的中文说明（UI 对话框逐条列出）
REASON_TEXT = {
    REASON_INVALID_PAPER: "缺少有效的纸张/标签尺寸（mm），无法核对 1:1 输出",
    REASON_PAPER_SIZE_MISMATCH: (
        "PDF 页面尺寸与所选纸张不一致且未允许转换，"
        "禁止静默拉伸/压缩"
    ),
    REASON_PAPER_CONVERTED: (
        "已按用户确认转换：将按 PDF 实际尺寸 1:1 输出"
    ),
    REASON_TRUE_SIZE_NOT_DECLARED: (
        "该设备未声明支持 1:1 物理尺寸；" + HONESTY_REMINDER
    ),
    REASON_TRUE_SIZE_UNKNOWN: (
        "目标物理尺寸无法由软件确认；" + HONESTY_REMINDER
    ),
}


@dataclass(frozen=True)
class ScaleRisk:
    """一次缩放风险分级结果。

    - ``level``：``ok`` / ``warn`` / ``block``；
    - ``reasons``：命中的原因码列表（按判定顺序）；
    - ``code``：对应既有校验码（PAPER_SIZE_MISMATCH / SCALE_MISMATCH /
      空），供适配器映射到 ValidationIssue；
    - ``message``：汇总文案，**不得**包含“已应用”等暗示软件控制
      驱动/硬件的措辞。
    """

    level: str
    reasons: tuple[str, ...] = ()
    code: str = ""
    message: str = ""

    @property
    def blocked(self) -> bool:
        return self.level == RISK_BLOCK

    @property
    def warned(self) -> bool:
        return self.level == RISK_WARN

    def reason_texts(self) -> list[str]:
        """逐条中文原因（UI 对话框列表用）。"""
        return [REASON_TEXT.get(r, r) for r in self.reasons]


def _paper_valid(paper: PaperSize | None) -> bool:
    return paper is not None and paper.is_valid


def _matches(pdf_w: float, pdf_h: float, paper: PaperSize,
             tolerance_mm: float) -> bool:
    """PDF 实测页与纸张是否在容差内一致（含宽高互换的方向等价）。"""
    exact = (units.size_close(pdf_w, paper.width_mm, tolerance_mm)
             and units.size_close(pdf_h, paper.height_mm, tolerance_mm))
    swapped = (units.size_close(pdf_w, paper.height_mm, tolerance_mm)
               and units.size_close(pdf_h, paper.width_mm, tolerance_mm))
    return exact or swapped


def classify_scale_risk(
    pdf_w_mm: float,
    pdf_h_mm: float,
    paper: PaperSize | None,
    caps,
    *,
    allow_paper_conversion: bool = False,
    tolerance_mm: float = 0.5,
) -> ScaleRisk:
    """纯函数：按 PDF 实测页尺寸、目标纸张与设备能力分级缩放风险。

    参数：
    - ``pdf_w_mm`` / ``pdf_h_mm``：PDF 首页实测物理尺寸（mm）；
    - ``paper``：目标纸张/标签规格（PaperSize，可为 None）；
    - ``caps``：打印机能力（``PrinterCapabilities`` 或 None）；只读
      ``supports_true_size``，不修改；
    - ``allow_paper_conversion``：用户已明确确认按 PDF 实际尺寸转换；
    - ``tolerance_mm``：尺寸匹配容差（mm，含边界 <=）。

    分级（互斥主干，能力提示可追加）：
    1. 纸张无效 → ``block``（INVALID_PAPER / INVALID_JOB）；
    2. 尺寸超容差 + ``allow=False`` → ``block``（PAPER_SIZE_MISMATCH）；
    3. 尺寸超容差 + ``allow=True`` → ``warn``（PAPER_CONVERTED）；
    4. 尺寸容差内 + ``supports_true_size is True`` → ``ok``；
    5. 尺寸容差内 + ``supports_true_size is False`` →
       ``warn``（TRUE_SIZE_NOT_DECLARED）；
    6. 尺寸容差内 + caps 缺失 / ``supports_true_size is None`` →
       ``warn``（TRUE_SIZE_UNKNOWN，目标物理尺寸无法由软件确认）。

    说明：DPI（203/300/600/None）与可打印边距不改变本分级——分辨率
    影响渲染像素密度，可打印区域由能力表 ``can_print_size`` 与公共
    校验另行处理；本函数只判定“1:1 物理尺寸是否可被确认”。
    """
    tol = float(tolerance_mm) if tolerance_mm is not None else 0.5
    if tol < 0:
        tol = 0.5

    if not _paper_valid(paper):
        return ScaleRisk(
            level=RISK_BLOCK,
            reasons=(REASON_INVALID_PAPER,),
            code="INVALID_JOB",
            message="任务规格缺少有效的纸张/标签尺寸（mm），无法核对 1:1 输出。",
        )

    matched = _matches(pdf_w_mm, pdf_h_mm, paper, tol)

    if not matched:
        dims = (f"PDF 页面 {pdf_w_mm:.2f}×{pdf_h_mm:.2f}mm，"
                f"所选纸张 {paper.width_mm:.2f}×{paper.height_mm:.2f}mm"
                f"（容差 {tol:g}mm）")
        if not allow_paper_conversion:
            return ScaleRisk(
                level=RISK_BLOCK,
                reasons=(REASON_PAPER_SIZE_MISMATCH,),
                code="PAPER_SIZE_MISMATCH",
                message=(
                    f"{dims} 不一致且未允许纸张转换，禁止静默拉伸/压缩；"
                    "请核对 PDF 标签尺寸与所选纸张，"
                    "或在打印确认中明确允许按 PDF 实际尺寸转换。"
                ),
            )
        return ScaleRisk(
            level=RISK_WARN,
            reasons=(REASON_PAPER_CONVERTED,),
            code="PAPER_SIZE_MISMATCH",
            message=(
                f"按 PDF 实际尺寸 {pdf_w_mm:.2f}×{pdf_h_mm:.2f}mm 1:1 输出"
                "（已按用户确认转换）。" + HONESTY_REMINDER + "。"
            ),
        )

    # ---- 尺寸在容差内：能否确认物理 1:1 取决于设备能力声明 ----
    true_size = getattr(caps, "supports_true_size", None)
    if true_size is True:
        return ScaleRisk(
            level=RISK_OK,
            reasons=(),
            code="",
            message="PDF 页面尺寸与所选纸张一致，且设备声明支持 1:1 物理尺寸。",
        )

    if true_size is False:
        return ScaleRisk(
            level=RISK_WARN,
            reasons=(REASON_TRUE_SIZE_NOT_DECLARED,),
            code="SCALE_MISMATCH",
            message=REASON_TEXT[REASON_TRUE_SIZE_NOT_DECLARED] + "。",
        )

    # caps 缺失 / supports_true_size 为 None：目标物理尺寸无法由软件确认
    return ScaleRisk(
        level=RISK_WARN,
        reasons=(REASON_TRUE_SIZE_UNKNOWN,),
        code="SCALE_MISMATCH",
        message=REASON_TEXT[REASON_TRUE_SIZE_UNKNOWN] + "。",
    )
