"""打印预览几何计算：条码适配、标签布局、越界警告。

纯计算模块（无 GUI/打印依赖），供打印中心 UI、PDF 导出与测试共用。
保证：
- 只做等比缩放（统一模块宽度），禁止非等比拉伸；
- 保留静区；
- 明确报告越界与缩放不足风险。
"""

from ..barcode.encoder import module_count
from .templates import GS1_MIN_MODULE_MM, GS1_MAX_MODULE_MM

DEFAULT_QUIET_MODULES = 11
DEFAULT_SIDE_PADDING_MM = 2.0     # 标签内左右留白
DEFAULT_TEXT_HEIGHT_MM = 3.5      # 可读数字占用高度


def total_modules(barcode_type, quiet=DEFAULT_QUIET_MODULES):
    """含静区的模块总数。"""
    return module_count(barcode_type) + 2 * quiet


def fit_barcode(label_width_mm, label_height_mm, barcode_type, *,
                show_text=True, quiet=DEFAULT_QUIET_MODULES,
                side_padding=DEFAULT_SIDE_PADDING_MM,
                target_module_mm=None):
    """计算条码在标签内的等比适配结果。

    返回 dict：
        module_mm      采用的模块宽度（等比）
        barcode_w_mm   条码含静区宽度
        barcode_h_mm   条码区高度
        text_h_mm      数字区高度
        fits           是否完整放入标签
        warnings       风险提示列表
    规则：条码 + 静区 + 数字必须完整放入标签；超出时取标签允许的最大
    模块宽度并给出警告（不会非等比压缩）。
    """
    warnings = []
    total = total_modules(barcode_type, quiet)
    avail_w = label_width_mm - 2 * side_padding
    avail_h = label_height_mm - (DEFAULT_TEXT_HEIGHT_MM if show_text else 0.0)

    max_module = avail_w / total if total > 0 else 0.0
    module_mm = max_module
    if target_module_mm is not None:
        module_mm = min(target_module_mm, max_module)
    if module_mm < GS1_MIN_MODULE_MM:
        warnings.append(
            f"模块宽度 {module_mm:.3f}mm 低于 GS1 最低缩放 "
            f"{GS1_MIN_MODULE_MM:.3f}mm（80%），扫描可能失败"
        )
    if target_module_mm is not None and target_module_mm > GS1_MAX_MODULE_MM:
        warnings.append(
            f"目标模块宽度 {target_module_mm:.3f}mm 超过 GS1 最高缩放 "
            f"{GS1_MAX_MODULE_MM:.3f}mm（200%）"
        )

    barcode_w = total * module_mm
    barcode_h = min(avail_h, label_height_mm * 0.6)
    if avail_h <= 0:
        warnings.append("标签高度不足以容纳条码与数字")
        barcode_h = max(label_height_mm, 0.1)
    text_h = DEFAULT_TEXT_HEIGHT_MM if show_text else 0.0

    fits = (barcode_w <= avail_w + 1e-6) and (barcode_h + text_h <= label_height_mm + 1e-6)
    if barcode_w > avail_w + 1e-6:
        warnings.append(
            f"条码含静区宽度 {barcode_w:.1f}mm 超出标签可用宽度 {avail_w:.1f}mm"
        )
    if barcode_h + text_h > label_height_mm + 1e-6:
        warnings.append("条码与数字总高度超出标签高度")

    return {
        "module_mm": module_mm,
        "barcode_w_mm": barcode_w,
        "barcode_h_mm": barcode_h,
        "text_h_mm": text_h,
        "fits": fits,
        "warnings": warnings,
    }


def compute_layout(template):
    """计算整页布局：每个标签 slot 的位置、内容缩放与越界警告。

    返回 dict：
        slots       [(row, col, x_mm, y_mm, fits, warnings), ...]
        overflow    是否存在越界标签
        warnings    页面级警告列表
    """
    page_warnings = []
    positions = template.label_positions()
    slots = []
    overflow = False
    for row, col, x, y in positions:
        x2 = x + template.label_width
        y2 = y + template.label_height
        if x2 > template.paper_width + 1e-6 or y2 > template.paper_height + 1e-6 \
                or x < -1e-6 or y < -1e-6:
            overflow = True
        slots.append({
            "row": row, "col": col, "x_mm": x, "y_mm": y,
            "fits": (x2 <= template.paper_width + 1e-6
                     and y2 <= template.paper_height + 1e-6),
            "warnings": [],
        })
    if overflow:
        page_warnings.append(
            "存在超出页面边界的标签，请减小行列数、标签尺寸或边距"
        )
    errors, tmpl_warnings = template.validate()
    page_warnings.extend(tmpl_warnings)
    return {"slots": slots, "overflow": overflow, "warnings": page_warnings}


def assign_items_to_slots(layout, items, copies=1):
    """把编码按顺序填入标签 slot（每项重复 copies 份）。

    返回 [(item, slot), ...]；超出 slot 数量的项被丢弃并计数。
    """
    slots = layout["slots"]
    filled = []
    idx = 0
    for item in items:
        for _ in range(max(copies, 1)):
            if idx >= len(slots):
                return filled, len(items) * max(copies, 1) - idx
            filled.append((item, slots[idx]))
            idx += 1
    return filled, 0
