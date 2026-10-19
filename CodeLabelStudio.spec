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
                   'client.printing.adapters.zpl', 'client.printing.adapters.tspl',
                   'client.printing.adapters.cpcl', 'client.printing.adapters.universal',
                   'client.printing.adapters.registry', 'client.printing.adapters.transports',
                   'win32print', 'win32api', 'PySide6.QtPdf'],
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
