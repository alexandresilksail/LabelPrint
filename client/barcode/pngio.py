"""最小 PNG 读写实现（8 位灰度、无滤波器、零第三方依赖）。

仅覆盖本项目渲染器产出的文件形态：
- 8 位灰度、颜色类型 0；
- 每行使用 filter type 0（None）；
- 单 IDAT。

该实现让"渲染 -> 扫描解码"自动化往返测试不依赖 Pillow，
保证条码核心在任何 Python 3.12+ 环境下可独立验证。
"""

import struct
import zlib

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _chunk(tag, data):
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(path, width, height, rows):
    """写入 8 位灰度 PNG。rows 为可迭代行，每行 bytes/bytearray，长度 width。"""
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type: None
        raw.extend(row)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(_PNG_SIG)
        f.write(_chunk(b"IHDR", ihdr))
        f.write(_chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(_chunk(b"IEND", b""))


def read_png_gray(path):
    """读取 8 位灰度 PNG，返回 list[bytearray]。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != _PNG_SIG:
        raise ValueError("不是有效的 PNG 文件")
    pos = 8
    idat = bytearray()
    width = height = None
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        pos += 4
        tag = data[pos : pos + 4]
        pos += 4
        payload = data[pos : pos + length]
        pos += length + 4  # 跳过 CRC
        if tag == b"IHDR":
            width, height, depth, ctype = struct.unpack(">IIBB", payload[:10])
            if depth != 8 or ctype != 0:
                raise ValueError("仅支持 8 位灰度 PNG")
        elif tag == b"IDAT":
            idat += payload
        elif tag == b"IEND":
            break
    if width is None or height is None:
        raise ValueError("PNG 缺少 IHDR")
    raw = zlib.decompress(bytes(idat))
    rows = []
    stride = width
    for y in range(height):
        start = y * (stride + 1)
        ftype = raw[start]
        if ftype != 0:
            raise ValueError(f"不支持的 PNG 滤波器: {ftype}")
        rows.append(bytearray(raw[start + 1 : start + 1 + stride]))
    return rows
