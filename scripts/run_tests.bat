@echo off
chcp 65001 >nul
cd /d %~dp0..
if "%PYTHON%"=="" set PYTHON=python
echo Using Python: %PYTHON%
%PYTHON% -m unittest discover -s tests -t . -v
