@echo off
chcp 65001 >nul
cd /d %~dp0..
if "%PYTHON%"=="" set PYTHON=python
echo Using Python: %PYTHON%
echo.
echo ===== 客户端测试（tests/） =====
if "%QT_QPA_PLATFORM%"=="" set QT_QPA_PLATFORM=offscreen
%PYTHON% -m unittest discover -s tests -t . -v
if errorlevel 1 goto failed
echo.
echo ===== 服务端测试（tests_server/） =====
%PYTHON% -m unittest discover -s tests_server -t . -v
if errorlevel 1 goto failed
echo.
echo ALL TESTS PASSED
exit /b 0
:failed
echo TEST FAILURES DETECTED
exit /b 1
