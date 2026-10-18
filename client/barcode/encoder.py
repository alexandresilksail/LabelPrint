"""UPC/EAN 标准编码器：将数字编码为模块（0/1）图案。

EAN-13 结构（95 模块）:
    起始 101 | 左侧 6 位（L/G 混合）| 中间 01010 | 右侧 6 位（R）| 结束 101

UPC-A 与 EAN-13 前置 "0" 的模块图案完全一致，人眼可读数字为 12 位。
EAN-8 结构（67 模块）:
    起始 101 | 左侧 4 位（L）| 中间 01010 | 右侧 4 位（R）| 结束 101

码表内在关系（tests/test_encoder.py 自动验证）:
    R = L 按位取反；G = R 反转。
"""

from .checkdigit import BARCODE_LENGTHS, validate

# A 码（左侧奇校验 L）
L_CODES = {
    "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101",
    "4": "0100011", "5": "0110001", "6": "0101111", "7": "0111011",
    "8": "0110111", "9": "0001011",
}
# B 码（左侧偶校验 G）
G_CODES = {
    "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001",
    "4": "0011101", "5": "0111001", "6": "0000101", "7": "0010001",
    "8": "0001001", "9": "0010111",
}
# C 码（右侧 R）
R_CODES = {
    "0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010",
    "4": "1011100", "5": "1001110", "6": "1010000", "7": "1000100",
    "8": "1001000", "9": "1110100",
}
# EAN-13 首位数字决定左侧 6 位使用 L 还是 G
PARITY_PATTERNS = {
    "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL",
    "4": "LGLLGG", "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG",
    "8": "LGLGGL", "9": "LGGLGL",
}
PARITY_REVERSE = {v: k for k, v in PARITY_PATTERNS.items()}

GUARD_START = "101"
GUARD_CENTER = "01010"
GUARD_END = "101"


def module_count(barcode_type):
    """返回不含静区的模块总数。"""
    if barcode_type not in BARCODE_LENGTHS:
        raise ValueError(f"不支持的条码类型: {barcode_type}")
    return 67 if barcode_type == "EAN-8" else 95


def _encode_ean13(value13):
    parity = PARITY_PATTERNS[value13[0]]
    left = ""
    for digit, p in zip(value13[1:7], parity):
        left += (L_CODES if p == "L" else G_CODES)[digit]
    right = "".join(R_CODES[d] for d in value13[7:])
    return GUARD_START + left + GUARD_CENTER + right + GUARD_END


def _encode_ean8(value8):
    left = "".join(L_CODES[d] for d in value8[:4])
    right = "".join(R_CODES[d] for d in value8[4:])
    return GUARD_START + left + GUARD_CENTER + right + GUARD_END


def encode_modules(value, barcode_type):
    """将完整编码转换为模块图案字符串（不含静区）。"""
    validate(value, barcode_type)
    if barcode_type == "UPC-A":
        return _encode_ean13("0" + value)
    if barcode_type == "EAN-13":
        return _encode_ean13(value)
    if barcode_type == "EAN-8":
        return _encode_ean8(value)
    raise ValueError(f"不支持的条码类型: {barcode_type}")


def encode_with_quiet_zone(value, barcode_type, quiet=11):
    """返回含左右静区的完整模块图案。静区不少于 11 模块（EAN 规范建议值）。"""
    if quiet < 0:
        raise ValueError("静区宽度不能为负")
    modules = encode_modules(value, barcode_type)
    return "0" * quiet + modules + "0" * quiet
