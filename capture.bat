@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title 采集电脑播放的声音 - 实时字幕

rem ==========================================================
rem  先双击 start.bat 把服务开起来，再双击本文件
rem ==========================================================
set "LANGUAGE=zh"
rem  zh=中文，auto=自动识别，en=英文 ...
rem  想录固定时长：在下面命令末尾加 --duration 60
rem  想换采集设备：加 --device "扬声器 (XXX)"
rem ==========================================================

set "PYTHONPATH="
set "PYTHONHOME="
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

set "NVLIBS="
for /d %%D in ("%~dp0runtime\python\Lib\site-packages\nvidia\*") do (
    if exist "%%D\bin" set "NVLIBS=!NVLIBS!%%D\bin;"
)
if defined NVLIBS set "PATH=!NVLIBS!%PATH%"
if exist "%~dp0bin\ffmpeg.exe" set "PATH=%~dp0bin;!PATH!"
set "PATH=%~dp0runtime\python;%~dp0runtime\python\Scripts;%PATH%"

echo ==========================================================
echo    采集「电脑正在播放的声音」并实时转成字幕
echo    用的是 WASAPI 环回，不需要虚拟声卡
echo ==========================================================
echo.
echo  [i] 按 Ctrl+C 停止，字幕会自动存到 logs\ 目录
echo  [i] 底部会有一条电平条：说话时它会跳动，说明抓到了声音
echo  [i] 别在这个窗口里拖选文字，Windows 会暂停输出
echo.

"%~dp0runtime\python\python.exe" "%~dp0tools\capture_system_audio.py" --language %LANGUAGE%

echo.
pause
endlocal
