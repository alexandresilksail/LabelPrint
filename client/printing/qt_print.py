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

from . import units
from .capabilities import (
    PaperSize,
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
        QPageLayout,
        QPageSize,
        QPainter,
        QRectF,
        QSize,
        QSizeF,
    )
    # QMarginsF 在 Qt6 位于 QtCore，部分版本仍在 QtGui，两处兜底
    try:
        from PySide6.QtGui import QMarginsF
    except ImportError:  # pragma: no cover - 版本差异分支
        from PySide6.QtCore import QMarginsF
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


def resolve_effective_page(
    pdf_w_mm: float,
    pdf_h_mm: float,
    spec: LabelJobSpec,
) -> tuple[PaperSize | None, str | None, PrintError | None]:
    """纯函数（无 Qt 依赖）：按 PDF 实测首页尺寸决定"有效页面"。

    三态返回 ``(effective_page, note, error)``：

    1. 实测尺寸与 ``spec.paper`` 在 ``spec.scale_tolerance_mm`` 容差内一致
       （含旋转后宽高互换）→ ``(spec.paper, None, None)``；
    2. 不一致且 ``allow_paper_conversion=False`` →
       ``(None, None, PrintError(PAPER_SIZE_MISMATCH))``，**禁止静默缩放**；
    3. 不一致且 ``allow_paper_conversion=True`` → 以 PDF 实测尺寸为有效页面，
       返回 ``(实测 PaperSize, 说明文案, None)``，注明已按用户确认转换。

    本函数只做尺寸比对，不打开文件、不触碰 Qt。
    """
    paper = spec.paper
    if paper is None or not paper.is_valid:
        return None, None, PrintError(
            PrintError.INVALID_JOB,
            resolution="任务规格缺少有效的纸张/标签尺寸（mm）。",
        )

    tol = spec.scale_tolerance_mm
    exact = (units.size_close(pdf_w_mm, paper.width_mm, tol)
             and units.size_close(pdf_h_mm, paper.height_mm, tol))
    swapped = (units.size_close(pdf_w_mm, paper.height_mm, tol)
               and units.size_close(pdf_h_mm, paper.width_mm, tol))
    if exact or swapped:
        return paper, None, None

    if spec.allow_paper_conversion:
        effective = PaperSize("pdf-page", pdf_w_mm, pdf_h_mm)
        # 与 scale_policy 分级一致：转换属 warn 级，须附驱动缩放诚信提示，
        # 不得宣称软件控制第三方驱动缩放。
        note = (
            f"按 PDF 实际尺寸 {pdf_w_mm:.2f}×{pdf_h_mm:.2f}mm 1:1 输出"
            "（已按用户确认转换）。PDF 页面尺寸正确不代表物理标签输出"
            "尺寸正确；第三方驱动缩放不受本软件控制，请先以测试页复核。"
        )
        return effective, note, None

    error = PrintError(
        PrintError.PAPER_SIZE_MISMATCH,
        f"PDF 页面尺寸 {pdf_w_mm:.2f}×{pdf_h_mm:.2f}mm 与所选纸张 "
        f"{paper.width_mm:.2f}×{paper.height_mm:.2f}mm 不一致"
        f"（容差 {tol}mm），未允许纸张转换，禁止静默拉伸/压缩。",
        resolution="请核对 PDF 标签尺寸与所选纸张是否一致；"
                   "或在打印确认中明确允许按 PDF 实际尺寸转换。",
    )
    return None, None, error


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

    流程：setPrinterName -> ``QPdfDocument.pagePointSize`` 逐页实测（pt→mm 用
    ``units.pt_to_mm``）-> :func:`resolve_effective_page` 冻结有效页面（不一致
    且未许可转换直接 ``PAPER_SIZE_MISMATCH`` 失败，**禁止静默拉伸/压缩**）->
    单个 ``setPageLayout`` 原子设置精确毫米页 + 方向（不再分调 setPageSize /
    setOrientation，避免 ExactMatch 下二次换向）-> setResolution(spec.dpi 或
    原生 DPI) -> 份数/质量映射 -> QPdfDocument 逐页渲染 QImage -> 按
    ``units.mm_to_dots`` 换算的精确设备点矩形绘制。painter 启动失败或
    printer.state() 为 Aborted/Error 时返回 ``PrintResult.failed``；成功只返回
    queued。

    份数规则（唯一应用点）：``printer.setCopyCount(max(1, spec.copies))`` 是
    任务份数的唯一应用位置；PDF 内容本身不含任务份数，渲染/绘制环节不得
    重复放大或复制页面。

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

    # ---- 尺寸冻结：先实测首页并逐页比对，不一致未许可即失败，不进入渲染 ----
    def _page_mm(index: int) -> tuple[float, float]:
        size_pt = doc.pagePointSize(index)
        return (units.pt_to_mm(size_pt.width()),
                units.pt_to_mm(size_pt.height()))

    first_w_mm, first_h_mm = _page_mm(0)
    effective_page, note, size_error = resolve_effective_page(
        first_w_mm, first_h_mm, spec)
    if size_error is not None:
        doc.close()
        return PrintResult.failed(printer_name, size_error)

    tol = spec.scale_tolerance_mm
    for page_index in range(1, doc.pageCount()):
        w_mm, h_mm = _page_mm(page_index)
        if not (units.size_close(w_mm, first_w_mm, tol)
                and units.size_close(h_mm, first_h_mm, tol)):
            doc.close()
            return PrintResult.failed(
                printer_name,
                PrintError(
                    PrintError.PAPER_SIZE_MISMATCH,
                    f"PDF 第 {page_index + 1} 页尺寸 "
                    f"{w_mm:.2f}×{h_mm:.2f}mm 与首页 "
                    f"{first_w_mm:.2f}×{first_h_mm:.2f}mm 不一致，"
                    "禁止按不同尺寸混排输出。",
                    resolution="请确保 PDF 所有页面尺寸一致后再提交。",
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

    # 方向先由 units.orient_paper 归一为物理尺寸，再用单个 setPageLayout
    # 原子下发页面+方向，避免 setPageSize 后 setOrientation 二次换向。
    physical = units.orient_paper(effective_page, spec.orientation)
    qt_orientation = (
        QPageLayout.Landscape
        if spec.orientation == JobOrientation.LANDSCAPE
        else QPageLayout.Portrait
    )
    page_size = QPageSize(
        QSizeF(physical.width_mm, physical.height_mm),
        QPageSize.Millimeter,
        QPageSize.ExactMatch,
    )
    printer.setPageLayout(
        QPageLayout(
            page_size,
            qt_orientation,
            QMarginsF(0.0, 0.0, 0.0, 0.0),
            QPageLayout.Millimeter,
        )
    )
    target_dpi = spec.dpi or printer.defaultResolution()
    if target_dpi:
        printer.setResolution(target_dpi)
    # 任务份数唯一应用点：PDF 内容不含任务份数，此处不复制页面。
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
            # 渲染像素与绘制矩形统一用 units.mm_to_dots 换算，禁止自乘 25.4
            px_w = max(1, units.mm_to_dots(physical.width_mm, dpi))
            px_h = max(1, units.mm_to_dots(physical.height_mm, dpi))
            image: QImage = doc.render(page_index, QSize(px_w, px_h))
            # 设备像素坐标下的精确毫米矩形，等比绘制，不拉伸
            rect = QRectF(
                0.0,
                0.0,
                float(units.mm_to_dots(physical.width_mm, dpi)),
                float(units.mm_to_dots(physical.height_mm, dpi)),
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

    message = None
    if note:
        message = (
            f"已提交到打印队列：{printer_name}。{note}。"
            "提交成功不代表物理打印完成，请查看打印机输出。"
        )
    return PrintResult.queued(printer_name, None, message=message)
