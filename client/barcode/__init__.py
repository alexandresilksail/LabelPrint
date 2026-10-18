"""条码核心：校验位计算、标准编码、PNG 渲染与扫描解码。

模块划分：
- checkdigit.py : UPC-A / EAN-13 / EAN-8 校验位计算与合法性验证
- encoder.py    : 按 GS1 标准将数字编码为模块（0/1）图案
- renderer.py   : 将模块图案渲染为 PNG 预览图（含人眼可读数字）
- decoder.py    : 从 PNG 像素还原模块图案并解码（自动化扫描测试用）
- pngio.py      : 最小 PNG 读写实现（8 位灰度，零第三方依赖）
- service.py    : 条码业务服务（生成 / 批量 / 范围分配 / 导入 / 状态机）
"""

from .checkdigit import (
    BARCODE_LENGTHS,
    BarcodeValidationError,
    calculate_check_digit,
    is_valid,
    normalize,
    validate,
)
from .encoder import encode_modules, encode_with_quiet_zone, module_count

__all__ = [
    "BARCODE_LENGTHS",
    "BarcodeValidationError",
    "calculate_check_digit",
    "is_valid",
    "normalize",
    "validate",
    "encode_modules",
    "encode_with_quiet_zone",
    "module_count",
]
