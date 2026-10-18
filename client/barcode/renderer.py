"""条码渲染器：将编码结果渲染为 PNG 预览图。

特性：
- 模块宽度、条码高度可调；
- 保留 EAN 规范要求的左右静区（默认 11 模块）；
- 可选择显示/隐藏条码下方人眼可读数字（内置 3x5 点阵字体）；
- 只做等比渲染：1 个模块 = module_width 像素，绝不压缩/拉伸条码本身。
"""

from .encoder import encode_with_quiet_zone
from .pngio import write_png

# 3x5 点阵数字字体
FONT_3X5 = {
    "0": ("111", "101", "101", "101", "111"),
    "1": ("010", "010", "010", "010", "010"),
    "2": ("111", "001", "111", "100", "111"),
    "3": ("111", "001", "111", "001", "111"),
    "4": ("101", "101", "111", "001", "001"),
    "5": ("111", "100", "111", "001", "111"),
    "6": ("111", "100", "111", "101", "111"),
    "7": ("111", "001", "001", "001", "001"),
    "8": ("111", "101", "111", "101", "111"),
    "9": ("111", "101", "111", "001", "111"),
}


def _text_groups(value, barcode_type):
    """按格式分组的可读数字（模拟标准印字位置：首位/左半/右半）。"""
    if barcode_type == "EAN-13":
        return [value[0], value[1:7], value[7:]]
    if barcode_type == "UPC-A":
        return [value[:6], value[6:]]
    return [value[:4], value[4:]]  # EAN-8


def _draw_char(rows, x, y, ch, scale):
    pattern = FONT_3X5.get(ch)
    if pattern is None:
        return
    for ry, line in enumerate(pattern):
        for rx, bit in enumerate(line):
            if bit == "1":
                for dy in range(scale):
                    for dx in range(scale):
                        rows[y + ry * scale + dy][x + rx * scale + dx] = 0


def render_png(
    value,
    barcode_type,
    path,
    *,
    module_width=3,
    bar_height=120,
    show_text=True,
    quiet_zone=11,
    text_scale=3,
):
    """渲染条码 PNG，返回图像元信息 dict。

    参数：
        module_width: 单个模块像素宽度（所有模块等宽，保证可扫描）
        bar_height:   条码区像素高度
        show_text:    是否在条码下方绘制可读数字
        quiet_zone:   静区模块数（左右各一份）
        text_scale:   可读数字点阵放大倍数
    """
    if module_width < 1 or bar_height < 1:
        raise ValueError("module_width 与 bar_height 必须为正整数")
    modules = encode_with_quiet_zone(value, barcode_type, quiet_zone)

    barcode_w = len(modules) * module_width
    margin = 8 * module_width

    char_w, char_h, spacing, group_gap = 3 * text_scale, 5 * text_scale, text_scale, 5 * text_scale
    text_w = 0
    if show_text:
        groups = _text_groups(value, barcode_type)
        text_w = (
            sum(len(g) * char_w + (len(g) - 1) * spacing for g in groups)
            + (len(groups) - 1) * group_gap
        )

    text_h = 5 * text_scale if show_text else 0
    text_block = text_h + 6 if show_text else 0
    width = max(barcode_w, text_w) + 2 * margin
    height = margin + bar_height + text_block + margin

    rows = [bytearray(b"\xff" * width) for _ in range(height)]

    x0 = (width - barcode_w) // 2
    for i, m in enumerate(modules):
        if m == "1":
            bx = x0 + i * module_width
            for x in range(bx, bx + module_width):
                for y in range(margin, margin + bar_height):
                    rows[y][x] = 0

    if show_text:
        tx = (width - text_w) // 2
        ty = margin + bar_height + 6
        for gi, g in enumerate(groups):
            for ch in g:
                _draw_char(rows, tx, ty, ch, text_scale)
                tx += char_w + spacing
            if gi < len(groups) - 1:
                tx += group_gap - spacing

    write_png(path, width, height, rows)
    return {
        "path": str(path),
        "width": width,
        "height": height,
        "margin": margin,
        "bar_height": bar_height,
        "module_width": module_width,
        "module_count": len(modules),
        "quiet_zone": quiet_zone,
    }
