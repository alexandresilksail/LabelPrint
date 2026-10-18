"""CSV 批量导入导出（含公式注入防护）。

安全设计（规格阶段 10）：以 = + - @ 制表符/回车开头的单元格在导出时
加单引号前缀，防止被 Excel/WPS 等表格软件解释为公式执行。
导入：限制文件大小与行数，拒绝明显异常的文件。
"""

import csv
import io
import os

MAX_IMPORT_BYTES = 10 * 1024 * 1024   # 10 MB
MAX_IMPORT_ROWS = 100000

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(text):
    """CSV 公式注入防护：危险前缀加 ' 转义。"""
    if text is None:
        return ""
    s = str(text)
    if s.startswith(_FORMULA_PREFIXES):
        return "'" + s
    return s


def export_records_csv(records, path):
    """导出编码清单 CSV。records 为 dict 列表。返回写入行数。

    列：barcode_value, barcode_type, source_type, authorization_reference,
        allocation_status, product_name, notes, created_at
    """
    columns = [
        "barcode_value", "barcode_type", "source_type",
        "authorization_reference", "allocation_status",
        "product_name", "notes", "created_at",
    ]
    count = 0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for rec in records:
            writer.writerow([sanitize_cell(rec.get(c) or "") for c in columns])
            count += 1
    return count


def read_import_csv(path):
    """读取导入 CSV，返回首列字符串列表（已去空白、去空行）。

    限制：文件大小 <= 10MB，行数 <= 100000，超出抛出 ValueError。
    自动识别 utf-8-sig（带 BOM）与 gbk 编码。
    """
    if not os.path.isfile(path):
        raise ValueError(f"导入文件不存在: {path}")
    size = os.path.getsize(path)
    if size > MAX_IMPORT_BYTES:
        raise ValueError(
            f"导入文件过大: {size / 1024 / 1024:.1f} MB（上限 "
            f"{MAX_IMPORT_BYTES // 1024 // 1024} MB）"
        )
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("无法解码导入文件（仅支持 UTF-8 / GBK 编码）")

    values = []
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        if not row:
            continue
        cell = (row[0] or "").strip()
        if not cell or cell.lower() in ("barcode_value", "barcode", "编码", "条码"):
            continue  # 跳过表头
        values.append(cell)
        if len(values) > MAX_IMPORT_ROWS:
            raise ValueError(f"导入行数超过上限 {MAX_IMPORT_ROWS}")
    return values
