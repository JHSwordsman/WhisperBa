@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title WhisperLiveKit Portable - 安装

echo ==========================================================
echo   WhisperLiveKit Portable 安装程序
echo   会自动下载 Python 3.12、依赖、模型，全程不需要你装东西
echo ==========================================================
echo.

if not exist "runtime\python\python.exe" goto :get_python
echo [i] 已检测到 runtime\python，跳过 Python 下载
goto :bootstrap

:get_python
echo [1/3] 下载 Python 3.12.10（约 11 MB）...
if exist "python-embed.zip" del /f /q "python-embed.zip"

set "PY_URL=https://mirrors.huaweicloud.com/python/3.12.10/python-3.12.10-embed-amd64.zip"
curl -L --http1.1 --ssl-no-revoke -o "python-embed.zip" "%PY_URL%"
if not exist "python-embed.zip" (
    echo.
    echo [x] 下载失败，改用备用地址重试...
    powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip' -OutFile 'python-embed.zip' -UseBasicParsing } catch { exit 1 }"
)
if not exist "python-embed.zip" (
    echo.
    echo [x] Python 下载失败，请检查网络后重跑 install.bat
    pause
    exit /b 1
)

echo [2/3] 解压到 runtime\python ...
if not exist "runtime" mkdir "runtime"
powershell -NoProfile -Command "Expand-Archive -Path 'python-embed.zip' -DestinationPath 'runtime\python' -Force"
del /f /q "python-embed.zip"

if not exist "runtime\python\python.exe" (
    echo.
    echo [x] 解压失败，请手动把 python-3.12.10-embed-amd64.zip 解压到 runtime\python
    pause
    exit /b 1
)

:bootstrap
echo [3/3] 安装依赖与模型（约 2 GB，视网速 10~30 分钟）...
echo.
"runtime\python\python.exe" "tools\bootstrap.py"

if errorlevel 1 (
    echo.
    echo [x] 安装未完成，看上面的提示。可以重跑本脚本，已完成的会跳过。
    pause
    exit /b 1
)

echo.
echo 按任意键关闭（之后双击 float.bat 使用）
pause >nul
exit /b 0
