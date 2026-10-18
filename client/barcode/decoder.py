"""条码解码器：从 PNG 像素还原模块图案并解码为数字。

用于自动化"扫描解码"测试：渲染出的 PNG 不经过任何中间表示，
直接从像素列重建模块串，再按 EAN 规则解码，验证与原始数字一致。

真实硬件扫描器的光学特性（分辨率、噪声、角度）不在此模拟范围内；
硬件验证属于人工验收步骤（见审计报告风险清单）。
"""

from math import gcd

from . import encoder as _encoder
from .checkdigit import validate
from .pngio import read_png_gray


class DecodeError(Exception):
    """扫描解码失败。"""


def _match_code(bits, table):
    for digit, code in table.items():
        if code == bits:
            return digit
    return None


def decode_modules(modules):
    """解码模块图案字符串（可含静区），返回 {'type','digits','valid'}。"""
    if not modules:
        raise DecodeError("模块图案为空")
    core = modules.strip("0")
    if not core.startswith(_encoder.GUARD_START) or not core.endswith(_encoder.GUARD_END):
        raise DecodeError("未找到起始/结束保护符")
    body = core[3:-3]

    if len(body) == 89:  # EAN-13 / UPC-A
        left_bits, center, right_bits = body[:42], body[42:47], body[47:]
        if center != _encoder.GUARD_CENTER:
            raise DecodeError("中间保护符不匹配")
        parities = []
        left_digits = []
        for i in range(6):
            bits = left_bits[i * 7 : (i + 1) * 7]
            digit = _match_code(bits, _encoder.L_CODES)
            if digit is not None:
                parities.append("L")
            else:
                digit = _match_code(bits, _encoder.G_CODES)
                parities.append("G")
            if digit is None:
                raise DecodeError(f"左侧第 {i + 1} 位无法解码: {bits}")
            left_digits.append(digit)
        first = _encoder.PARITY_REVERSE.get("".join(parities))
        if first is None:
            raise DecodeError(f"未知奇偶模式: {''.join(parities)}")
        right_digits = []
        for i in range(6):
            bits = right_bits[i * 7 : (i + 1) * 7]
            digit = _match_code(bits, _encoder.R_CODES)
            if digit is None:
                raise DecodeError(f"右侧第 {i + 1} 位无法解码: {bits}")
            right_digits.append(digit)
        digits = first + "".join(left_digits) + "".join(right_digits)
        validate(digits, "EAN-13")
        return {"type": "EAN-13", "digits": digits, "valid": True}

    if len(body) == 61:  # EAN-8
        left_bits, center, right_bits = body[:28], body[28:33], body[33:]
        if center != _encoder.GUARD_CENTER:
            raise DecodeError("中间保护符不匹配")
        digits = ""
        for i in range(4):
            bits = left_bits[i * 7 : (i + 1) * 7]
            digit = _match_code(bits, _encoder.L_CODES)
            if digit is None:
                raise DecodeError(f"左侧第 {i + 1} 位无法解码: {bits}")
            digits += digit
        for i in range(4):
            bits = right_bits[i * 7 : (i + 1) * 7]
            digit = _match_code(bits, _encoder.R_CODES)
            if digit is None:
                raise DecodeError(f"右侧第 {i + 1} 位无法解码: {bits}")
            digits += digit
        validate(digits, "EAN-8")
        return {"type": "EAN-8", "digits": digits, "valid": True}

    raise DecodeError(f"无法识别的模块图案长度: {len(body)}")


def decode_png(path, *, band=None):
    """从 PNG 文件解码条码。

    band: 可选 (y0, y1)，仅扫描该纵向区间的像素（用于排除可读数字行）。
    """
    rows = read_png_gray(path)
    if band is not None:
        rows = rows[band[0] : band[1]]
    if not rows:
        raise DecodeError("空图像")
    w, h = len(rows[0]), len(rows)

    dark = [any(rows[y][x] < 128 for y in range(h)) for x in range(w)]
    first = next((i for i, d in enumerate(dark) if d), None)
    if first is None:
        raise DecodeError("未检测到条码图案")
    last = max(i for i, d in enumerate(dark) if d)

    runs = []
    cur, cnt = dark[first], 0
    for d in dark[first : last + 1]:
        if d == cur:
            cnt += 1
        else:
            runs.append((cur, cnt))
            cur, cnt = d, 1
    runs.append((cur, cnt))

    module_width = 0
    for _, c in runs:
        module_width = gcd(module_width, c)
    if module_width <= 0:
        raise DecodeError("无法推断模块宽度")

    modules = "".join(("1" if v else "0") * (c // module_width) for v, c in runs)
    result = decode_modules(modules)
    result["module_width_px"] = module_width
    result["module_count"] = len(modules)
    return result
