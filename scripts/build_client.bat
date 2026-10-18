@echo off
REM ============================================================
REM CodeLabelStudio Windows 客户端打包脚本
REM 用法（在仓库根目录执行）：
REM   scripts\build_client.bat
REM 产出：
REM   dist\CodeLabelStudio\          绿色便携版目录
REM   dist\CodeLabelStudio-Portable.zip
REM 依赖：pip install pyinstaller
REM ============================================================
setlocal
set PYTHON=%1
if "%PYTHON%"=="" set PYTHON=python

echo [1/4] 清理旧产物...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/4] PyInstaller 打包...
%PYTHON% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --name CodeLabelStudio ^
    --windowed ^
    --onefile ^
    --add-data "client\assets;client\assets" ^
    --hidden-import client.barcode.encoder ^
    --hidden-import client.barcode.checkdigit ^
    --hidden-import client.barcode.renderer ^
    --hidden-import client.barcode.svg ^
    --hidden-import client.barcode.csvio ^
    --hidden-import client.database.db ^
    --hidden-import client.printing.pdf ^
    --hidden-import client.licensing.client_license ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.QtQml ^
    run_client.py
if errorlevel 1 goto :error

echo [3/4] 生成便携版压缩包...
powershell -NoProfile -Command "Compress-Archive -Path 'dist\CodeLabelStudio.exe' -DestinationPath 'dist\CodeLabelStudio-Portable.zip' -Force"
if errorlevel 1 goto :error

echo [4/4] 完成。
echo   单文件可执行：dist\CodeLabelStudio.exe
echo   便携版：      dist\CodeLabelStudio-Portable.zip
echo.
echo 注意：客户端数据默认保存在 exe 同级 data\ 目录（首次运行自动创建）。
goto :end

:error
echo.
echo [ERROR] 打包失败，请检查上方 PyInstaller 输出。
exit /b 1

:end
endlocal
