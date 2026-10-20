"""PDF 导出（ReportLab 矢量输出）。

- render_pdf()           单个条码的独立 PDF 页面（物理尺寸精确到 mm）
- render_label_sheet_pdf() 按标签模板排版的多标签 PDF（支持跨页）

所有输出为真实矢量 PDF 文档（%PDF 头 + 页面内容流），
不是改扩展名的位图。
"""

from ..barcode.encoder import encode_with_quiet_zone, module_count
from ..barcode.renderer import _text_groups
from .capabilities import JobOrientation, PaperSize
from .preview import fit_barcode
from .templates import LabelTemplate
from .units import orient_paper

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
               show_text=True, quiet=DEFAULT_QUIET_MODULES, margin_mm=5.0,
               offset_x_mm: float | None = None,
               offset_y_mm: float | None = None,
               target_module_mm: float | None = None):
    """单个条码 PDF。页面尺寸 = 内容物理尺寸 + 边距。返回 meta dict。

    可选校准参数（默认 None，保持向后兼容）：
    - offset_x_mm/offset_y_mm：整体平移条码绘制原点（mm；y 向下为正）。
    - target_module_mm：目标模块宽度；未显式给出 ``module_mm`` 时采用。
    - quiet：静区模块数，进入编码（encode_with_quiet_zone）。
    """
    total = module_count(barcode_type) + 2 * quiet
    if module_mm is None:
        module_mm = target_module_mm if target_module_mm is not None else 0.33
    barcode_w = total * module_mm
    text_h = 4.0 if show_text else 0.0
    page_w = barcode_w + 2 * margin_mm
    page_h = bar_height_mm + text_h + 2 * margin_mm

    ox = float(offset_x_mm) if offset_x_mm is not None else 0.0
    oy = float(offset_y_mm) if offset_y_mm is not None else 0.0

    c = rl_canvas.Canvas(path, pagesize=(page_w * mm, page_h * mm))
    c.setTitle(f"{barcode_type} {value}")
    _draw_barcode(
        c, value, barcode_type, margin_mm + ox, page_h - margin_mm - oy,
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
        "offset_x_mm": ox,
        "offset_y_mm": oy,
        "quiet": quiet,
        "path": str(path),
    }


def render_label_sheet_pdf(items, template, path, *,
                           quiet=DEFAULT_QUIET_MODULES,
                           side_padding_mm=2.0, font_mm=3.0,
                           offset_x_mm: float | None = None,
                           offset_y_mm: float | None = None,
                           target_module_mm: float | None = None,
                           paper_override: PaperSize | None = None,
                           orientation_override: JobOrientation | None = None
                           ) -> dict:
    """按标签模板排版 PDF。items: 原始编码 [(value, barcode_type), ...]。

    份数规则（冻结）：本函数是模板份数（``template.copies``）的**唯一展开点**；
    ``items`` 必须传入**未展开**的原始编码，函数内部按 ``template.copies``
    展开一次。任务份数（``LabelJobSpec.copies``）绝不进入 PDF 渲染（由 Qt
    setCopyCount / 打印协议唯一应用），本函数签名层面不接受 copies 参数。

    校准语义：
    - ``offset_x_mm``/``offset_y_mm``：校准偏移，**叠加**到
      ``template.offset_x``/``template.offset_y``（final = template.offset +
      传入值）；slot 位置已含 template.offset，故仅把传入增量作用于每个 slot
      的实际绘制坐标（``slot["x_mm"] += 传入增量``）。
    - ``quiet``：静区模块数（来自配置），替换默认 DEFAULT_QUIET_MODULES，同时
      进入 fit_barcode 与编码（encode_with_quiet_zone）。
    - ``target_module_mm``：校准模块宽度，传给 fit_barcode（等比、不超过标签
      容量上限）。

    纸张语义：
    - ``paper_override``：用户选择纸张。渲染前用 units.orient_paper（结合
      ``orientation_override``）归一，页面尺寸克隆为其值；**若标签网格超出
      该纸张 → 抛 ValueError（明确拒绝，禁止静默裁切/缩放）**，消息含所选纸张
      与网格尺寸；放得下则 PDF 页面尺寸 == override 尺寸。
    - ``orientation_override``：按方向归一纸张尺寸（units.orient_paper），
      PDF 页面尺寸随之确定；不在别处二次换向。

    返回 meta：pages / labels_per_page / labels_total / dropped / warnings /
    path，另含 template_copies、expanded_total（= len(items)×template_copies）、
    slot_positions（[(row, col, x_mm, y_mm, value)]，x/y 为偏移生效后的实际
    绘制原点）、paper_width_mm/paper_height_mm（实际渲染页面尺寸）。
    """
    from .preview import compute_layout

    if not isinstance(template, LabelTemplate):
        template = LabelTemplate.from_dict(template)
    errors, warnings = template.validate()
    if errors:
        raise ValueError("模板参数非法: " + "; ".join(errors))

    # ---- 纸张归一：override / 换向统一在 units.orient_paper 完成，不二次换向 ----
    if paper_override is not None:
        effective = paper_override
        if orientation_override is not None:
            effective = orient_paper(effective, orientation_override)
    elif orientation_override is not None:
        effective = orient_paper(
            PaperSize("template", template.paper_width, template.paper_height),
            orientation_override,
        )
    else:
        effective = PaperSize("template", template.paper_width,
                              template.paper_height)
    page_w, page_h = effective.width_mm, effective.height_mm

    # ---- 网格容纳校验：纸张被更换/换向时明确拒绝，禁止静默裁切/缩放 ----
    positions = template.label_positions()
    max_right = max((x + template.label_width for _, _, x, _ in positions),
                    default=0.0)
    max_bottom = max((y + template.label_height for _, _, _, y in positions),
                     default=0.0)
    grid_w, grid_h = template.grid_size_mm()
    paper_changed = paper_override is not None or \
        orientation_override is not None
    if paper_changed and (max_right > page_w + 1e-6 or max_bottom > page_h + 1e-6):
        raise ValueError(
            f"所选纸张 {effective.name} {page_w:.1f}×{page_h:.1f}mm 无法容纳"
            f"标签网格（网格实际占用 {max_right:.1f}×{max_bottom:.1f}mm，"
            f"标称网格 {grid_w:.1f}×{grid_h:.1f}mm），请更换纸张或减小"
            "行列数/标签尺寸"
        )

    layout = compute_layout(template)
    warnings.extend(layout["warnings"])

    # ---- 模板份数唯一展开点：items 为原始编码，此处展开一次 ----
    copies = max(int(template.copies), 1)
    expanded = []
    for item in items:
        for _ in range(copies):
            expanded.append(item)
    expanded_total = len(expanded)

    slots = layout["slots"]
    slots_per_page = len(slots)
    if slots_per_page <= 0:
        pages = 0
        dropped = expanded_total
    else:
        pages = max(1, -(-expanded_total // slots_per_page))
        if pages > MAX_SHEET_PAGES:
            pages = MAX_SHEET_PAGES
            warnings.append(
                f"超过单次最大 {MAX_SHEET_PAGES} 页，"
                f"仅生成前 {MAX_SHEET_PAGES * slots_per_page} 个标签"
            )
        dropped = max(0, expanded_total - pages * slots_per_page)
    if dropped:
        warnings.append(f"{dropped} 个编码超出模板容量，未排入")

    # 校准偏移叠加：final = template.offset + 传入值；slot 已含 template.offset
    extra_x = float(offset_x_mm) if offset_x_mm is not None else 0.0
    extra_y = float(offset_y_mm) if offset_y_mm is not None else 0.0
    final_offset_x = template.offset_x + extra_x
    final_offset_y = template.offset_y + extra_y

    c = rl_canvas.Canvas(
        path, pagesize=(page_w * mm, page_h * mm)
    )
    c.setTitle(f"标签打印 {template.template_name}")

    slot_positions = []
    for page_idx in range(pages):
        chunk = expanded[page_idx * slots_per_page:(page_idx + 1) * slots_per_page]
        for item, slot in zip(chunk, slots):
            value, barcode_type = item
            fit = fit_barcode(
                template.label_width, template.label_height, barcode_type,
                show_text=template.show_text, quiet=quiet,
                side_padding=side_padding_mm,
                target_module_mm=target_module_mm,
            )
            module_mm = fit["module_mm"]
            bar_h = min(fit["barcode_h_mm"], template.barcode_height)
            # 标签内垂直居中偏上：数字在条码下方
            content_h = bar_h + (font_mm + 0.6 if template.show_text else 0.0)
            # 偏移生效后的 slot 实际绘制原点（页面左上坐标系）
            draw_x = slot["x_mm"] + extra_x
            draw_y = slot["y_mm"] + extra_y
            top_y = draw_y + (template.label_height - content_h) / 2 + content_h
            left_x = draw_x + side_padding_mm
            _draw_barcode(
                c, value, barcode_type, left_x, top_y,
                module_mm=module_mm, bar_h_mm=bar_h,
                show_text=template.show_text, quiet=quiet, font_mm=font_mm,
            )
            slot_positions.append(
                (slot["row"], slot["col"], draw_x, draw_y, value)
            )
        c.showPage()
    c.save()

    return {
        "pages": pages,
        "labels_per_page": slots_per_page,
        "labels_total": expanded_total - dropped,
        "dropped": dropped,
        "warnings": warnings,
        "path": str(path),
        "template_copies": copies,
        "expanded_total": expanded_total,
        "slot_positions": slot_positions,
        "paper_width_mm": page_w,
        "paper_height_mm": page_h,
        "offset_x_mm": final_offset_x,
        "offset_y_mm": final_offset_y,
    }
