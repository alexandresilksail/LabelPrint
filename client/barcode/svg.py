"""SVG 矢量条码导出。

生成真实的矢量内容（rect 表示条，text 表示可读数字），
不是把 PNG 改扩展名冒充矢量图。
"""

from .encoder import encode_with_quiet_zone, module_count
from .renderer import _text_groups


def _svg_number(x, v):
    """格式化数值，避免 -0 与超长小数。"""
    if abs(v) < 1e-9:
        return "0"
    return f"{v:.4f}".rstrip("0").rstrip(".")


def render_svg(
    value,
    barcode_type,
    path=None,
    *,
    module_width=0.33,
    bar_height=15.0,
    show_text=True,
    quiet_zone=11,
    font_size=3.0,
):
    """渲染条码为 SVG 字符串；给定 path 时同时写入文件。返回 (svg, meta)。

    单位：毫米（mm），与打印模板体系统一。
    module_width: 模块宽度（mm）。GS1 标称 0.33mm（100% 缩放），
                  允许 0.264~0.660mm（80%~200% 缩放）。
    bar_height:   条码区高度（mm）。
    font_size:    可读数字字号（mm 字高）。
    """
    if module_width <= 0:
        raise ValueError("module_width 必须为正数")
    if bar_height <= 0:
        raise ValueError("bar_height 必须为正数")
    modules = encode_with_quiet_zone(value, barcode_type, quiet_zone)
    total_modules = len(modules)

    text_h = font_size + 1.0 if show_text else 0.0
    width = total_modules * module_width
    height = bar_height + text_h

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_svg_number(0, width)}mm" '
        f'height="{_svg_number(0, height)}mm" '
        f'viewBox="0 0 {_svg_number(0, width)} {_svg_number(0, height)}">',
        f'<rect x="0" y="0" width="{_svg_number(0, width)}" '
        f'height="{_svg_number(0, height)}" fill="#ffffff"/>',
        f'<g fill="#000000">',
    ]
    x = 0.0
    for m in modules:
        if m == "1":
            parts.append(
                f'<rect x="{_svg_number(0, x)}" y="0" '
                f'width="{_svg_number(0, module_width)}" '
                f'height="{_svg_number(0, bar_height)}" fill="#000000"/>'
            )
        x += module_width
    parts.append("</g>")

    if show_text:
        groups = _text_groups(value, barcode_type)
        # 按标准印字位置把数字分三组放置在条码下方
        # EAN-13: 首位在左侧静区外，其余分居中央两侧
        char_w = font_size * 0.62
        group_gap = font_size * 1.6
        if barcode_type == "EAN-13":
            left_group, right_group = groups[1], groups[2]
            first = groups[0]
            left_w = len(left_group) * char_w
            barcode_w = module_count(barcode_type) * module_width
            start_x = quiet_zone * module_width
            center_x = start_x + barcode_w / 2
            # 首位数字（左上静区外侧）——按视觉顺序先输出
            parts.append(
                f'<text x="{_svg_number(0, max(0.0, start_x - char_w - font_size * 0.4))}" '
                f'y="{_svg_number(0, bar_height + font_size * 0.8)}" '
                f'font-family="monospace" font-size="{_svg_number(0, font_size)}" '
                f'fill="#000000">{first}</text>'
            )
            # 左半数字（首位后 6 位）
            lx = center_x - group_gap / 2 - left_w
            parts.append(
                f'<text x="{_svg_number(0, lx)}" '
                f'y="{_svg_number(0, bar_height + font_size * 0.8)}" '
                f'font-family="monospace" font-size="{_svg_number(0, font_size)}" '
                f'fill="#000000">{left_group}</text>'
            )
            # 右半数字
            parts.append(
                f'<text x="{_svg_number(0, center_x + group_gap / 2)}" '
                f'y="{_svg_number(0, bar_height + font_size * 0.8)}" '
                f'font-family="monospace" font-size="{_svg_number(0, font_size)}" '
                f'fill="#000000">{right_group}</text>'
            )
        else:
            groups_text = groups if isinstance(groups, list) else [groups]
            total_w = sum(len(g) * char_w for g in groups_text) + \
                (len(groups_text) - 1) * group_gap
            tx = max(0.0, (width - total_w) / 2)
            for g in groups_text:
                parts.append(
                    f'<text x="{_svg_number(0, tx)}" '
                    f'y="{_svg_number(0, bar_height + font_size * 0.8)}" '
                    f'font-family="monospace" font-size="{_svg_number(0, font_size)}" '
                    f'fill="#000000">{g}</text>'
                )
                tx += len(g) * char_w + group_gap
    parts.append("</svg>")
    svg = "\n".join(parts)

    if path:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)

    meta = {
        "barcode_value": value,
        "barcode_type": barcode_type,
        "width_mm": width,
        "height_mm": height,
        "module_width_mm": module_width,
        "bar_height_mm": bar_height,
        "quiet_zone_modules": quiet_zone,
        "total_modules": total_modules,
    }
    return svg, meta
