"""PDF 尺寸测量、缩放检测与预览渲染（pypdf 纯 Python 部分 Linux 可测）。

- :func:`measure_pdf_pages`     用 pypdf 读 MediaBox/CropBox，pt -> mm，处理 /Rotate
- :func:`detect_scale_mismatch`  首页实测尺寸 vs 目标纸张，超容差判非预期缩放
- :func:`rasterize_preview`      Qt QPdfDocument 渲染 PNG 预览（条件导入；
                                Linux / 无 PySide6 抛 PrintError）

PDF 坐标点（point）换算：1 mm = 25.4 / 72 pt（PostScript 基准 72 dpi）。
"""

from __future__ import annotations

import sys
import tempfile

from .capabilities import PaperSize
from .errors import PrintError
from .job import ScaleReport

PT_TO_MM = 25.4 / 72.0

# ---- Qt 条件导入（仅供 rasterize_preview 使用）----
try:  # pragma: no cover
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtGui import QImage, QSize
    _QT_PREVIEW = True
except ImportError:
    QPdfDocument = None  # type: ignore[assignment,misc]
    QImage = None  # type: ignore[assignment,misc]
    QSize = None  # type: ignore[assignment,misc]
    _QT_PREVIEW = False


def measure_pdf_pages(path: str) -> list[dict]:
    """逐页测量 PDF 物理尺寸（毫米）。

    返回 ``[{page, width_mm, height_mm, rotation}, ...]``，page 从 1 开始。
    优先采用 CropBox（实际可视区域），缺省回退 MediaBox；/Rotate 为 90/270
    时交换宽高，使返回值反映旋转后的真实铺纸尺寸。
    """
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages: list[dict] = []
    for index, page in enumerate(reader.pages, start=1):
        box = page.cropbox if page.cropbox is not None else page.mediabox
        width_pt = float(box.width)
        height_pt = float(box.height)
        rotation = int(page.rotation or 0)
        if rotation in (90, 270):
            width_pt, height_pt = height_pt, width_pt
        pages.append({
            "page": index,
            "width_mm": width_pt * PT_TO_MM,
            "height_mm": height_pt * PT_TO_MM,
            "rotation": rotation,
        })
    return pages


def detect_scale_mismatch(path: str, paper: PaperSize,
                          tolerance_mm: float = 0.5) -> ScaleReport:
    """检测 PDF 首页是否按目标纸张 1:1 排版（非预期缩放检测）。

    首页实测宽/高与目标纸张偏差均 <= tolerance_mm 时 ok=True；
    任一方向超容差则 ok=False 并给出 SCALE_MISMATCH 类警告。多页时以首页
    为准，并在 warnings 注明。
    """
    pages = measure_pdf_pages(path)
    if not pages:
        raise PrintError(
            PrintError.INVALID_JOB,
            resolution=f"PDF 无有效页面：{path}",
        )
    first = pages[0]
    actual_w = first["width_mm"]
    actual_h = first["height_mm"]
    dev_x = abs(actual_w - paper.width_mm)
    dev_y = abs(actual_h - paper.height_mm)
    ok = dev_x <= tolerance_mm and dev_y <= tolerance_mm

    warnings: list[str] = []
    if not ok:
        warnings.append(
            f"检测到非预期缩放：首页实测 {actual_w:.2f}×{actual_h:.2f}mm，"
            f"目标 {paper.width_mm:.2f}×{paper.height_mm:.2f}mm，"
            f"偏差 {dev_x:.2f}/{dev_y:.2f}mm 超出 {tolerance_mm}mm 容差，"
            "标签可能无法按 1:1 打印"
        )
    if len(pages) > 1:
        warnings.append(
            f"PDF 共 {len(pages)} 页，缩放检测以首页尺寸为基准。"
        )
    return ScaleReport(
        expected_width_mm=paper.width_mm,
        expected_height_mm=paper.height_mm,
        actual_width_mm=actual_w,
        actual_height_mm=actual_h,
        tolerance_mm=tolerance_mm,
        ok=ok,
        warnings=warnings,
    )


def rasterize_preview(path: str, dpi: int = 150,
                      max_pages: int = 1) -> list[str]:
    """用 Qt QPdfDocument 把前 max_pages 页渲染为 PNG，返回文件路径列表。

    非 Windows 或未安装 PySide6 时抛 PrintError（UNSUPPORTED_PLATFORM /
    QT_BACKEND_UNAVAILABLE）；本函数不负责打印，仅用于预览。
    """
    if sys.platform != "win32":
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="Qt 预览渲染仅在 Windows + PySide6 环境可用；"
                       "当前平台无法渲染 PDF 预览。",
        )
    if not _QT_PREVIEW:
        raise PrintError(
            PrintError.QT_BACKEND_UNAVAILABLE,
            resolution="缺少 PySide6（Qt），无法渲染 PDF 预览。",
        )

    doc = QPdfDocument()
    if doc.load(str(path)) != QPdfDocument.Status.Ready:
        raise PrintError(
            PrintError.SPOOL_FAILED,
            resolution=f"PDF 加载失败，无法渲染预览：{path}",
        )
    out_dir = tempfile.mkdtemp(prefix="label_preview_")
    paths: list[str] = []
    try:
        count = min(int(max_pages), doc.pageCount())
        for index in range(count):
            page = doc.page(index)
            size_pt = page.size()  # QSizeF，单位为 point（72 dpi）
            px_w = max(1, round(size_pt.width() / 72.0 * dpi))
            px_h = max(1, round(size_pt.height() / 72.0 * dpi))
            image: QImage = doc.render(index, QSize(px_w, px_h))
            out_path = f"{out_dir}/page_{index + 1}.png"
            image.save(out_path, "PNG")
            paths.append(out_path)
    finally:
        doc.close()
    return paths
