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
from .templates import GS1_MODULE_MM, LabelTemplate
from .units import orient_paper, quiet_zone_mm_to_modules

try:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas as rl_canvas
except ImportError as exc:  # pragma: no cover
    raise ImportError("PDF 导出需要 reportlab：pip install reportlab") from exc

DEFAULT_QUIET_MODULES = 11
MAX_SHEET_PAGES = 100        # 单次生成安全页数上限，超出部分报 dropped

#: 硬件参数（走纸/间隙传感/打印浓度）不由软件直接控制的统一标注
HARDWARE_NOT_CONTROLLED = "不由软件直接控制（打印机硬件/驱动面板参数）"

#: 已知硬件参数键 → 中文参数名（软件无法直接控制，仅如实登记未应用）
_HARDWARE_LABELS = {
    "feed_mm": "走纸长度（进纸）",
    "feed_length_mm": "走纸长度（进纸）",
    "gap_sensor": "间隙传感模式",
    "gap_sensor_mode": "间隙传感模式",
    "print_darkness": "打印浓度（温度/黑度）",
    "darkness": "打印浓度（温度/黑度）",
}

#: 标签物理间隙的 meta 备注（证明它未进入条码布局）
LABEL_GAP_NOTE = ("标签物理间隙（走纸/定位用，仅专用协议与设备硬件相关），"
                  "未参与条码静区或条码内容布局")


def _resolve_quiet_modules(quiet_zone_mm, quiet, module_for_conversion):
    """静区模块数解析（优先级冻结）：

    1. ``quiet_zone_mm``（毫米，用户配置）→ 经 units 换算为模块数；
    2. 否则用 ``quiet``（模块数，既有参数）；
    3. 都未给 → GS1 默认 11 模块。

    ``quiet_zone_mm`` 给出时必须提供正数换算模块宽，否则换算函数显式
    抛 ``ValueError``，绝不静默丢弃或回退。
    """
    if quiet_zone_mm is not None:
        return quiet_zone_mm_to_modules(quiet_zone_mm, module_for_conversion)
    if quiet is not None:
        return int(quiet)
    return DEFAULT_QUIET_MODULES


def _collect_unapplied(hardware_params) -> list[str]:
    """把渲染器无法应用的硬件参数如实列入 unapplied_params（诚信红线）。

    走纸、间隙传感、打印浓度等软件无法直接控制的参数，逐项标注
    "不由软件直接控制"，禁止在 UI 显示为"已应用"。
    """
    unapplied: list[str] = []
    if not hardware_params:
        return unapplied
    for name, value in hardware_params.items():
        label = _HARDWARE_LABELS.get(name, name)
        unapplied.append(f"{label}（{name}={value}）：{HARDWARE_NOT_CONTROLLED}")
    return unapplied


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
               target_module_mm: float | None = None,
               quiet_zone_mm: float | None = None,
               module_mm_override: float | None = None,
               label_gap_mm: float = 0.0,
               hardware_params: dict | None = None):
    """单个条码 PDF。页面尺寸 = 内容物理尺寸 + 边距。返回 meta dict。

    可选校准参数（默认 None，保持向后兼容）：
    - offset_x_mm/offset_y_mm：整体平移条码绘制原点（mm；y 向下为正）。
    - target_module_mm / module_mm_override：目标模块宽度（后者为别名）；
      未显式给出 ``module_mm`` 时采用。
    - quiet：静区模块数，进入编码（encode_with_quiet_zone）。
    - quiet_zone_mm：静区物理宽度（mm，用户配置）。优先级高于 ``quiet``：
      给出时经 units 换算为模块数再进入编码；换算模块宽取已解析的
      ``module_mm``/``target_module_mm``，缺省用 GS1 标称 0.33mm。
    - label_gap_mm：标签物理间隙（走纸/定位硬件参数），仅原样记录到
      ``label_gap_mm_passthrough``，**不参与**条码静区或内容布局。
    - hardware_params：软件无法控制的硬件参数（走纸/间隙传感/打印浓度），
      逐项进入 ``unapplied_params`` 并标注"不由软件直接控制"，禁止静默丢弃。
    """
    # 模块宽解析：显式 module_mm > module_mm_override > target_module_mm > 0.33
    if module_mm is None:
        module_mm = (module_mm_override if module_mm_override is not None
                    else target_module_mm)
    if module_mm is None:
        module_mm = GS1_MODULE_MM

    quiet_modules = _resolve_quiet_modules(quiet_zone_mm, quiet, module_mm)
    total = module_count(barcode_type) + 2 * quiet_modules
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
        show_text=show_text, quiet=quiet_modules,
    )
    c.showPage()
    c.save()
    return {
        "barcode_value": value,
        "barcode_type": barcode_type,
        "page_width_mm": page_w,
        "page_height_mm": page_h,
        "module_mm": module_mm,
        "module_mm_applied": module_mm,
        "bar_height_mm": bar_height_mm,
        "offset_x_mm": ox,
        "offset_y_mm": oy,
        "quiet": quiet_modules,
        "quiet_modules_applied": quiet_modules,
        "quiet_zone_mm_applied": quiet_modules * module_mm,
        "label_gap_mm_passthrough": float(label_gap_mm or 0.0),
        "label_gap_mm_note": LABEL_GAP_NOTE,
        "unapplied_params": _collect_unapplied(hardware_params),
        "path": str(path),
    }


def render_label_sheet_pdf(items, template, path, *,
                           quiet=DEFAULT_QUIET_MODULES,
                           side_padding_mm=2.0, font_mm=3.0,
                           offset_x_mm: float | None = None,
                           offset_y_mm: float | None = None,
                           target_module_mm: float | None = None,
                           paper_override: PaperSize | None = None,
                           orientation_override: JobOrientation | None = None,
                           quiet_zone_mm: float | None = None,
                           module_mm_override: float | None = None,
                           label_gap_mm: float = 0.0,
                           hardware_params: dict | None = None
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
    - ``target_module_mm`` / ``module_mm_override``：校准模块宽度（后者别名），
      传给 fit_barcode（等比、不超过标签容量上限）。
    - ``quiet_zone_mm``：静区物理宽度（mm，UI/配置层单位）。与 ``quiet``
      模块数参数**互斥优先**：给了 ``quiet_zone_mm`` 就经 units 换算为模块数
      （换算基准 = 已解析目标模块宽；未设置模块宽时先按默认静区 fit 取自由
      适配模块宽），否则用 ``quiet`` 模块数，再否则默认 11 模块。换算结果同时
      进入 fit_barcode 与 encode_with_quiet_zone，绝不静默丢弃。
    - ``label_gap_mm``：标签纸相邻标签的**物理间隙**（走纸/定位，仅专用协议
      TSPL GAP 等与设备硬件相关）。只原样记入 meta ``label_gap_mm_passthrough``，
      **严禁**加入条码静区或条码内容布局，本函数不把它传给 fit_barcode/编码。
    - ``hardware_params``：软件无法直接控制的硬件参数（走纸/间隙传感/打印
      浓度），逐项进入 meta ``unapplied_params`` 并标注"不由软件直接控制"；
      任何渲染器不支持/未使用的传入参数都必须在此或 warnings 中明确列出，
      禁止静默丢弃。

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

    配置生效回执（诚信展示，禁止把未应用项标成"已应用"）：
    - ``quiet_modules_applied``：实际进入编码/fit 的每侧静区模块数；
    - ``quiet_zone_mm_applied``：实际静区物理宽度（= 模块数 × 实际模块宽）；
    - ``module_mm_applied``：首个标签实际采用的模块宽度；
    - ``label_gap_mm_passthrough``：传入的物理间隙原值（未参与条码布局，
      见 ``label_gap_mm_note``）；
    - ``unapplied_params``：收集了但渲染器无法应用的参数（当前为走纸/间隙
      传感/打印浓度等硬件参数），逐项标注"不由软件直接控制"。
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

    # 目标模块宽：module_mm_override 别名优先，其次既有 target_module_mm
    target_module = (module_mm_override if module_mm_override is not None
                     else target_module_mm)

    # 硬件参数如实标记为未应用（走纸/间隙传感/打印浓度不由软件直接控制）
    unapplied_params = _collect_unapplied(hardware_params)

    c = rl_canvas.Canvas(
        path, pagesize=(page_w * mm, page_h * mm)
    )
    c.setTitle(f"标签打印 {template.template_name}")

    slot_positions = []
    # 首个标签的实际生效值，写入 meta 作为"配置真实生效"回执
    applied_quiet_modules: int | None = None
    applied_module_mm: float | None = None
    for page_idx in range(pages):
        chunk = expanded[page_idx * slots_per_page:(page_idx + 1) * slots_per_page]
        for item, slot in zip(chunk, slots):
            value, barcode_type = item
            # 静区解析：quiet_zone_mm(mm) > quiet(模块数) > 默认 11。
            # 给了 mm 但未设模块宽时，先按默认静区 fit 取自由适配模块宽作换算基准。
            if quiet_zone_mm is not None:
                conv_module = target_module
                if conv_module is None:
                    probe = fit_barcode(
                        template.label_width, template.label_height, barcode_type,
                        show_text=template.show_text,
                        quiet=DEFAULT_QUIET_MODULES,
                        side_padding=side_padding_mm,
                    )
                    conv_module = probe["module_mm"]
                quiet_modules = quiet_zone_mm_to_modules(quiet_zone_mm,
                                                        conv_module)
            else:
                quiet_modules = int(quiet) if quiet is not None \
                    else DEFAULT_QUIET_MODULES
            fit = fit_barcode(
                template.label_width, template.label_height, barcode_type,
                show_text=template.show_text, quiet=quiet_modules,
                side_padding=side_padding_mm,
                target_module_mm=target_module,
            )
            module_mm = fit["module_mm"]
            if applied_quiet_modules is None:
                applied_quiet_modules = quiet_modules
                applied_module_mm = module_mm
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
                show_text=template.show_text, quiet=quiet_modules,
                font_mm=font_mm,
            )
            slot_positions.append(
                (slot["row"], slot["col"], draw_x, draw_y, value)
            )
        c.showPage()
    c.save()

    # 无标签可渲染时回退默认回执，避免 meta 缺键
    if applied_quiet_modules is None:
        applied_quiet_modules = int(quiet) if quiet is not None \
            else DEFAULT_QUIET_MODULES
    if applied_module_mm is None:
        applied_module_mm = target_module if target_module is not None \
            else GS1_MODULE_MM

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
        # ---- 校准配置真实生效回执 ----
        "quiet_modules_applied": applied_quiet_modules,
        "quiet_zone_mm_applied": applied_quiet_modules * applied_module_mm,
        "module_mm_applied": applied_module_mm,
        # 物理间隙：仅直通记录，未参与条码布局
        "label_gap_mm_passthrough": float(label_gap_mm or 0.0),
        "label_gap_mm_note": LABEL_GAP_NOTE,
        # 收集了但渲染器无法应用的硬件参数（逐项标注不由软件直接控制）
        "unapplied_params": unapplied_params,
    }
