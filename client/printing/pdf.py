"""PDF 导出（ReportLab 矢量输出）。

- render_pdf()           单个条码的独立 PDF 页面（物理尺寸精确到 mm）
- render_label_sheet_pdf() 按标签模板排版的多标签 PDF（支持跨页）

所有输出为真实矢量 PDF 文档（%PDF 头 + 页面内容流），
不是改扩展名的位图。
"""

from ..barcode.encoder import encode_with_quiet_zone, module_count
from ..barcode.renderer import _text_groups
from .preview import fit_barcode
from .templates import LabelTemplate

try:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
except ImportError as exc:  # pragma: no cover
    raise ImportError("PDF 导出需要 reportlab：pip install reportlab") from exc

DEFAULT_QUIET_MODULES = 11
MAX_SHEET_PAGES = 100        # 单次生成安全页数上限，超出部分报 dropped


def _draw_barcode(c, value, barcode_type, x_mm, y_mm, *, module_mm, bar_h_mm,
                  show_text=True, quiet=DEFAULT_QUIET_MODULES, font_mm=3.0):
    """在 ReportLab 画布上绘制条码（原点已转换为 PDF 左下角坐标系）。

    x_mm, y_mm 为条码区左上角（mm，页面坐标系）。返回内容总高度 mm。
    """
    modules = encode_with_quiet_zone(value, barcode_type, quiet)
    total_w = len(modules) * module_mm

    # PDF 原点在左下；条码区顶部 y_mm -> 底部 y_pdf
    bars_bottom = y_mm - bar_h_mm
    c.setFillColorRGB(0, 0, 0)
    x = x_mm
    for m in modules:
        if m == "1":
            c.rect(x * mm, bars_bottom * mm, module_mm * mm, bar_h_mm * mm,
                   fill=1, stroke=0)
        x += module_mm

    if show_text:
        text_y = bars_bottom - font_mm * 0.2 - font_mm
        c.setFont("Helvetica", font_mm * mm * 0.75)
        groups = _text_groups(value, barcode_type)
        if barcode_type == "EAN-13":
            char_w = font_mm * 0.62
            center = x_mm + (module_count(barcode_type) * module_mm) / 2
            left_g, right_g, first = groups[1], groups[2], groups[0]
            c.drawString(
                (center - font_mm * 0.8 - len(left_g) * char_w) * mm,
                text_y * mm, left_g,
            )
            c.drawString((center + font_mm * 0.8) * mm, text_y * mm, right_g)
            c.drawString(
                max(0.0, x_mm - char_w - font_mm * 0.4) * mm, text_y * mm, first,
            )
        else:
            text = "".join(groups) if isinstance(groups, list) else groups
            c.drawCentredString(
                (x_mm + total_w / 2) * mm, text_y * mm, text
            )
        return bar_h_mm + font_mm + font_mm * 0.2
    return bar_h_mm


def render_pdf(value, barcode_type, path, *, module_mm=None, bar_height_mm=15.0,
               show_text=True, quiet=DEFAULT_QUIET_MODULES, margin_mm=5.0):
    """单个条码 PDF。页面尺寸 = 内容物理尺寸 + 边距。返回 meta dict。"""
    total = module_count(barcode_type) + 2 * quiet
    if module_mm is None:
        module_mm = 0.33
    barcode_w = total * module_mm
    text_h = 4.0 if show_text else 0.0
    page_w = barcode_w + 2 * margin_mm
    page_h = bar_height_mm + text_h + 2 * margin_mm

    c = rl_canvas.Canvas(path, pagesize=(page_w * mm, page_h * mm))
    c.setTitle(f"{barcode_type} {value}")
    _draw_barcode(
        c, value, barcode_type, margin_mm, page_h - margin_mm,
        module_mm=module_mm, bar_h_mm=bar_height_mm,
        show_text=show_text, quiet=quiet,
    )
    c.showPage()
    c.save()
    return {
        "barcode_value": value,
        "barcode_type": barcode_type,
        "page_width_mm": page_w,
        "page_height_mm": page_h,
        "module_mm": module_mm,
        "bar_height_mm": bar_height_mm,
        "path": str(path),
    }


def render_label_sheet_pdf(items, template, path, *, quiet=DEFAULT_QUIET_MODULES,
                           side_padding_mm=2.0, font_mm=3.0):
    """按标签模板排版 PDF。items: [(value, barcode_type), ...]。

    - 每标签等比缩放假条码 + 静区 + 可读数字；
    - 超出每页 slot 自动分页；
    - 返回 meta（页数、每页标签数、越界警告）。
    """
    from .preview import compute_layout

    if not isinstance(template, LabelTemplate):
        template = LabelTemplate.from_dict(template)
    errors, warnings = template.validate()
    if errors:
        raise ValueError("模板参数非法: " + "; ".join(errors))

    layout = compute_layout(template)
    warnings.extend(layout["warnings"])

    # 多页流：按 copies 展开 items，再按每页 slot 数切页
    slots = layout["slots"]
    slots_per_page = len(slots)
    copies = max(int(template.copies), 1)
    expanded = []
    for item in items:
        for _ in range(copies):
            expanded.append(item)

    if slots_per_page <= 0:
        pages = 0
        dropped = len(expanded)
    else:
        pages = max(1, -(-len(expanded) // slots_per_page))
        if pages > MAX_SHEET_PAGES:
            pages = MAX_SHEET_PAGES
            warnings.append(
                f"超过单次最大 {MAX_SHEET_PAGES} 页，"
                f"仅生成前 {MAX_SHEET_PAGES * slots_per_page} 个标签"
            )
        dropped = max(0, len(expanded) - pages * slots_per_page)
    if dropped:
        warnings.append(f"{dropped} 个编码超出模板容量，未排入")

    c = rl_canvas.Canvas(
        path, pagesize=(template.paper_width * mm, template.paper_height * mm)
    )
    c.setTitle(f"标签打印 {template.template_name}")

    for page_idx in range(pages):
        chunk = expanded[page_idx * slots_per_page:(page_idx + 1) * slots_per_page]
        for item, slot in zip(chunk, slots):
            value, barcode_type = item
            fit = fit_barcode(
                template.label_width, template.label_height, barcode_type,
                show_text=template.show_text, quiet=quiet,
                side_padding=side_padding_mm,
            )
            module_mm = fit["module_mm"]
            bar_h = min(fit["barcode_h_mm"], template.barcode_height)
            # 标签内垂直居中偏上：数字在条码下方
            content_h = bar_h + (font_mm + 0.6 if template.show_text else 0.0)
            top_y = slot["y_mm"] + (template.label_height - content_h) / 2 + content_h
            left_x = slot["x_mm"] + side_padding_mm
            _draw_barcode(
                c, value, barcode_type, left_x, top_y,
                module_mm=module_mm, bar_h_mm=bar_h,
                show_text=template.show_text, quiet=quiet, font_mm=font_mm,
            )
        c.showPage()
    c.save()

    return {
        "pages": pages,
        "labels_per_page": slots_per_page,
        "labels_total": len(expanded) - dropped,
        "dropped": dropped,
        "warnings": warnings,
        "path": str(path),
    }
