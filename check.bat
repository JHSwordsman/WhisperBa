@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title WhisperLiveKit 便携版 - 环境自检

set "PYTHONPATH="
set "PYTHONHOME="

set "NVLIBS="
for /d %%D in ("%~dp0runtime\python\Lib\site-packages\nvidia\*") do (
    if exist "%%D\bin" set "NVLIBS=!NVLIBS!%%D\bin;"
)
if defined NVLIBS set "PATH=!NVLIBS!%PATH%"
if exist "%~dp0bin\ffmpeg.exe" set "PATH=%~dp0bin;!PATH!"
set "PATH=%~dp0runtime\python;%~dp0runtime\python\Scripts;%PATH%"

"%~dp0runtime\python\python.exe" "%~dp0tools\selftest.py"

echo.
pause
endlocal
