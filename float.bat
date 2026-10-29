@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"

rem ==========================================================
rem  悬浮字幕条：录音 / 系统声音 / 截图 OCR
rem  双击即可，服务会自动拉起
rem ==========================================================

set "PYTHONPATH="
set "PYTHONHOME="
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"
set "HF_HOME=%~dp0runtime\cache\huggingface"
set "HF_ENDPOINT=https://hf-mirror.com"

set "NVLIBS="
for /d %%D in ("%~dp0runtime\python\Lib\site-packages\nvidia\*") do (
    if exist "%%D\bin" set "NVLIBS=!NVLIBS!%%D\bin;"
)
if defined NVLIBS set "PATH=!NVLIBS!%PATH%"
if exist "%~dp0bin\ffmpeg.exe" set "PATH=%~dp0bin;!PATH!"
set "PATH=%~dp0runtime\python;%~dp0runtime\python\Scripts;%PATH%"

start "" "%~dp0runtime\python\pythonw.exe" "%~dp0tools\float_app.py"
exit /b 0
