"""UPC/EAN 校验位计算与合法性验证。

标准规则（以不含校验位的基础数字长度 n = 完整长度 - 1 计算）：
- 自右向左，权重 3、1 交替，校验位本身权重为 1；
- 校验位 = (10 - 加权和 mod 10) mod 10。

覆盖格式：
- UPC-A : 12 位数字，前 11 位计算第 12 位校验位；
- EAN-13: 13 位数字，前 12 位计算第 13 位校验位；
- EAN-8 : 8  位数字，前 7  位计算第 8  位校验位。
"""

BARCODE_LENGTHS = {"UPC-A": 12, "EAN-13": 13, "EAN-8": 8}


class BarcodeValidationError(ValueError):
    """条码格式或校验位不合法。"""


def _require_ascii_digits(value, barcode_type):
    if not isinstance(value, str):
        raise BarcodeValidationError(f"{barcode_type} 编码必须是字符串")
    if not value:
        raise BarcodeValidationError(f"{barcode_type} 编码不能为空")
    for ch in value:
        if not ("0" <= ch <= "9"):
            raise BarcodeValidationError(
                f"{barcode_type} 编码包含非 ASCII 数字字符: {ch!r}"
            )


def calculate_check_digit(partial, barcode_type):
    """根据不含校验位的基础数字计算校验位。"""
    if barcode_type not in BARCODE_LENGTHS:
        raise BarcodeValidationError(f"不支持的条码类型: {barcode_type}")
    expected_len = BARCODE_LENGTHS[barcode_type] - 1
    _require_ascii_digits(partial, barcode_type)
    if len(partial) != expected_len:
        raise BarcodeValidationError(
            f"{barcode_type} 需要 {expected_len} 位基础数字（不含校验位），"
            f"实际收到 {len(partial)} 位"
        )
    n = len(partial)
    total = 0
    for i, ch in enumerate(partial):
        weight = 3 if (n - i) % 2 == 1 else 1
        total += int(ch) * weight
    return str((10 - total % 10) % 10)


def validate(value, barcode_type):
    """校验完整编码：仅数字、长度正确、校验位正确。"""
    if barcode_type not in BARCODE_LENGTHS:
        raise BarcodeValidationError(f"不支持的条码类型: {barcode_type}")
    full = BARCODE_LENGTHS[barcode_type]
    _require_ascii_digits(value, barcode_type)
    if len(value) != full:
        raise BarcodeValidationError(
            f"{barcode_type} 应为 {full} 位数字，实际 {len(value)} 位"
        )
    expected = calculate_check_digit(value[:-1], barcode_type)
    if value[-1] != expected:
        raise BarcodeValidationError(
            f"{barcode_type} 校验位错误: 应为 {expected}，实际 {value[-1]}"
        )
    return True


def is_valid(value, barcode_type):
    """便捷布尔判断。"""
    try:
        validate(value, barcode_type)
        return True
    except BarcodeValidationError:
        return False


def normalize(value, barcode_type):
    """规范化输入：缺少校验位时自动补全；完整编码则校验后原样返回。"""
    if barcode_type not in BARCODE_LENGTHS:
        raise BarcodeValidationError(f"不支持的条码类型: {barcode_type}")
    full = BARCODE_LENGTHS[barcode_type]
    _require_ascii_digits(value, barcode_type)
    if len(value) == full - 1:
        return value + calculate_check_digit(value, barcode_type)
    if len(value) == full:
        validate(value, barcode_type)
        return value
    raise BarcodeValidationError(
        f"{barcode_type} 应为 {full} 位（或 {full - 1} 位不含校验位），实际 {len(value)} 位"
    )
