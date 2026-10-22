# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run_client.py'],
    pathex=[],
    binaries=[],
    datas=[('client/assets', 'client/assets')],
    hiddenimports=['client.barcode.encoder', 'client.barcode.checkdigit', 'client.barcode.renderer', 'client.barcode.svg', 'client.barcode.csvio', 'client.database.db', 'client.printing.pdf', 'client.licensing.client_license',
                   # 打印子系统（契约/核心/校准/驱动/适配器；win32print 与 QtPdf 为 Windows 条件导入，显式声明避免打包遗漏）
                   'client.printing.capabilities', 'client.printing.errors',
                   'client.printing.job', 'client.printing.windows_print',
                   'client.printing.qt_print', 'client.printing.pdf_render',
                   'client.printing.calibration', 'client.printing.config_store',
                   'client.printing.driver_installer',
                   # 第四轮补充：P1 新增 Authenticode 签名验证模块（被 driver_installer 静态再导出，显式声明防遗漏）
                   'client.printing.authenticode',
                   # 第四轮补充（2026-10-22，只加不删）：静区/偏移单位换算、预览、会话锁、缩放分级
                   'client.printing.units', 'client.printing.preview',
                   'client.printing.print_session', 'client.printing.scale_policy',
                   'client.printing.adapters.zpl', 'client.printing.adapters.tspl',
                   'client.printing.adapters.cpcl', 'client.printing.adapters.universal',
                   'client.printing.adapters.registry', 'client.printing.adapters.transports',
                   # 第四轮补充：适配器基座与公共校验（base/_common，被三协议静态引用）
                   'client.printing.adapters.base', 'client.printing.adapters._common',
                   # 第四轮补充：打印导出页面 / 纯逻辑 / 设置向导（--printer-setup 为函数内惰性导入）
                   'client.ui.printer_setup', 'client.ui.pages.print_export',
                   'client.ui.pages.print_export_logic',
                   # 第四轮补充：条件导入的第三方 PDF 依赖（pdf.py/calibration.py 用 reportlab；pdf_render.py 用 pypdf）
                   'pypdf', 'reportlab',
                   'win32print', 'win32api', 'pywintypes',
                   'PySide6.QtPdf', 'PySide6.QtPrintSupport'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.Qt3DCore', 'PySide6.QtQml'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='CodeLabelStudio',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
