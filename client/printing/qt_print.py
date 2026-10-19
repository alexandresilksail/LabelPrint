"""Qt（PySide6）PDF 通用打印后端。

Windows 上 QPrinter 走系统驱动与打印队列：本路径是矢量 PDF 的通用打印主路径。
通过 ``QPageSize(QSizeF(w_mm, h_mm), QPageSize.Millimeter, QPageSize.ExactMatch)``
按精确毫米尺寸建页，禁止驱动自动缩放，保证 1:1 物理尺寸。

PySide6 采用条件导入：Linux / 未安装 PySide6 时模块仍可导入，系统调用入口抛
``PrintError(UNSUPPORTED_PLATFORM / QT_BACKEND_UNAVAILABLE)``。

诚信约定：成功只返回 ``PrintResult.queued``（"已提交到打印队列，不代表物理
打印完成"），绝不声称物理打印成功。

依赖：PySide6.QtPrintSupport（QPrinter/QPrinterInfo）、PySide6.QtPdf
（QPdfDocument）、PySide6.QtGui（QPageLayout/QPageSize/QPainter/QImage）。
调用方需已存在 QApplication（UI 主线程），本模块不自行创建。
"""

from __future__ import annotations

import os
import sys

from .capabilities import (
    PrinterCapabilities,
    Protocol,
    Transport,
)
from .errors import PrintError
from .job import JobOrientation, LabelJobSpec, PrintResult, PrintQuality

# ---- 条件导入 PySide6 ----
try:  # pragma: no cover - 仅在安装 PySide6 的环境命中
    from PySide6.QtPrintSupport import QPrinter, QPrinterInfo
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtGui import (
        QImage,
        QMarginsF,
        QPageLayout,
        QPageSize,
        QPainter,
        QRectF,
        QSize,
        QSizeF,
    )
    _QT_AVAILABLE = True
except ImportError:
    QPrinter = None  # type: ignore[assignment,misc]
    QPrinterInfo = None  # type: ignore[assignment,misc]
    QPdfDocument = None  # type: ignore[assignment,misc]
    _QT_AVAILABLE = False


def qt_print_available() -> bool:
    """当前环境是否可使用 Qt 打印后端（Windows 且 PySide6 可导入）。"""
    return sys.platform == "win32" and _QT_AVAILABLE


def _require_qt() -> None:
    if sys.platform != "win32":
        raise PrintError(
            PrintError.UNSUPPORTED_PLATFORM,
            resolution="Qt 打印后端在本交付中面向 Windows 队列；当前系统不可用。",
        )
    if not _QT_AVAILABLE:
        raise PrintError(
            PrintError.QT_BACKEND_UNAVAILABLE,
            resolution="缺少 PySide6，请执行 pip install PySide6 后重试。",
        )


def _quality_to_qt(quality: PrintQuality):
    """PrintQuality -> QPrinter 质量枚举。"""
    if quality == PrintQuality.DRAFT:
        return QPrinter.Draft
    if quality == PrintQuality.HIGH:
        return QPrinter.High
    return QPrinter.Normal


def list_qt_printers() -> list[PrinterCapabilities]:
    """枚举 Qt 可见的系统打印机（QPrinterInfo.availablePrinters）。

    Qt 不暴露端口名，transport 统一为 UNKNOWN；dpi 取 QPrinter 原生默认分辨率。
    平台/后端不可用时抛 PrintError。
    """
    _require_qt()
    default = QPrinterInfo.defaultPrinter()
    default_name = default.printerName() if default is not None else None

    result: list[PrinterCapabilities] = []
    for info in QPrinterInfo.availablePrinters():
        printer = QPrinter(info)
        dpi = printer.defaultResolution() or None
        result.append(PrinterCapabilities(
            printer_name=info.printerName(),
            port_name=None,
            is_default=info.printerName() == default_name,
            transport=Transport.UNKNOWN,
            protocol=Protocol.UNIVERSAL,
            dpi=dpi,
            notes=(
                f"Qt 枚举（{info.makeAndModel() or info.description()}），"
                "未经真实设备实测"
            ),
            verified_with_hardware=False,
        ))
    return result


def print_pdf_via_qt(printer_name: str, pdf_path: str,
                     spec: LabelJobSpec) -> PrintResult:
    """把 PDF 通过 QPrinter 提交到系统队列（精确毫米、1:1、无自动缩放）。

    流程：setPrinterName -> QPageLayout + QPageSize.ExactMatch 建精确毫米页 ->
    setResolution(spec.dpi 或原生 DPI) -> 份数/方向/质量映射 -> QPdfDocument
    逐页渲染 QImage -> 按精确 mm 矩形绘制。painter 启动失败或 printer.state()
    为 Aborted/Error 时返回 ``PrintResult.failed``；成功只返回 queued。

    Windows 上 QPrinter 走系统驱动与队列，ExactMatch 确保无自动缩放。
    """
    _require_qt()

    if not spec.paper or not spec.paper.is_valid:
        return PrintResult.failed(
            printer_name,
            PrintError(
                PrintError.INVALID_JOB,
                resolution="任务规格缺少有效的纸张/标签尺寸（mm）。",
            ),
        )
    if not pdf_path or not os.path.isfile(pdf_path):
        return PrintResult.failed(
            printer_name,
            PrintError(
                PrintError.INVALID_JOB,
                resolution=f"PDF 文件不存在：{pdf_path}",
            ),
        )

    doc = QPdfDocument()
    load_status = doc.load(pdf_path)
    if load_status != QPdfDocument.Status.Ready or doc.pageCount() == 0:
        return PrintResult.failed(
            printer_name,
            PrintError(
                PrintError.SPOOL_FAILED,
                resolution=f"PDF 加载失败（status={load_status}）：{pdf_path}",
            ),
        )

    printer = QPrinter(QPrinter.HighResolution)
    printer.setPrinterName(printer_name)
    if not printer.printerName():
        return PrintResult.failed(
            printer_name,
            PrintError(PrintError.QUEUE_MISSING,
                       resolution=f"系统中找不到队列：{printer_name}"),
        )

    paper = spec.paper
    # 精确毫米页面，ExactMatch 阻止驱动按内置纸张自动缩放
    page_size = QPageSize(
        QSizeF(paper.width_mm, paper.height_mm),
        QPageSize.Millimeter,
        QPageSize.ExactMatch,
    )
    printer.setPageSize(page_size)
    orientation = (
        QPageLayout.Landscape
        if spec.orientation == JobOrientation.LANDSCAPE
        else QPageLayout.Portrait
    )
    printer.setOrientation(orientation)
    printer.setPageMargins(
        QMarginsF(0.0, 0.0, 0.0, 0.0), QPageLayout.Millimeter
    )
    target_dpi = spec.dpi or printer.defaultResolution()
    if target_dpi:
        printer.setResolution(target_dpi)
    printer.setCopyCount(max(1, int(spec.copies)))
    printer.setQuality(_quality_to_qt(spec.quality))

    painter = QPainter()
    if not painter.begin(printer):
        doc.close()
        return PrintResult.failed(
            printer_name,
            PrintError(PrintError.SPOOL_FAILED,
                       resolution="QPainter 无法在该打印机上启动。"),
        )

    try:
        dpi = printer.resolution() or target_dpi or 300
        for page_index in range(doc.pageCount()):
            if page_index > 0:
                printer.newPage()
            px_w = max(1, round(paper.width_mm / 25.4 * dpi))
            px_h = max(1, round(paper.height_mm / 25.4 * dpi))
            image: QImage = doc.render(page_index, QSize(px_w, px_h))
            # 设备像素坐标下的精确毫米矩形，等比绘制，不拉伸
            rect = QRectF(
                0.0,
                0.0,
                paper.width_mm / 25.4 * dpi,
                paper.height_mm / 25.4 * dpi,
            )
            painter.drawImage(rect, image)
    finally:
        painter.end()
        doc.close()

    state = printer.state()
    if state == QPrinter.Aborted:
        return PrintResult.failed(
            printer_name,
            PrintError(PrintError.DEVICE_OFFLINE,
                       resolution="打印任务被中止（设备离线或队列被中止）。"),
        )
    if state == QPrinter.Error:
        return PrintResult.failed(
            printer_name,
            PrintError(PrintError.SPOOL_FAILED,
                       resolution="打印机状态为 Error，任务未能进入队列。"),
        )

    return PrintResult.queued(
        printer_name, None,
        message=(
            f"已提交到打印队列：{printer_name}。"
            "已提交到打印队列，不代表物理打印完成，请查看打印机输出。"
        ),
    )
