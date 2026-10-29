@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title WhisperLiveKit 便携版 - 实时语音转写

rem ==========================================================
rem  可调参数：一般只要改这里
rem ==========================================================
set "PORT=8000"
set "MODEL=faster-whisper-large-v3-turbo"
set "LANGUAGE=auto"
rem  MODEL      models 目录下的模型文件夹名
rem             可选：faster-whisper-large-v3-turbo / faster-whisper-small
rem  LANGUAGE   auto=自动识别，zh=中文，en=英文，ja=日文 ...
rem  强制 CPU   ：在命令行先执行 set WLK_FORCE_CPU=1 再运行本脚本
rem ==========================================================

set "ROOT=%~dp0"
set "PY=%ROOT%runtime\python\python.exe"
set "MODEL_DIR=%ROOT%models\%MODEL%"

echo ==========================================================
echo    WhisperLiveKit 便携版
echo    目录: %ROOT%
echo ==========================================================
echo.

if not exist "%PY%" (
    echo [x] 找不到内置运行环境: %PY%
    echo     便携包可能没有解压完整，请重新解压。
    echo.
    pause
    exit /b 1
)

rem ---- 清掉外部 Python 环境变量干扰（本机装了别的 Python 也不影响）----
set "PYTHONPATH="
set "PYTHONHOME="
set "PIP_CONFIG_FILE="

rem ---- 本机回环不走系统代理（有代理软件时不加这句会连不上自己）----
set "NO_PROXY=127.0.0.1,localhost"
set "no_proxy=127.0.0.1,localhost"

rem ---- 模型缓存与镜像：万一要联网下载，走国内镜像 ----
set "HF_HOME=%ROOT%runtime\cache\huggingface"
set "HF_HUB_CACHE=%HF_HOME%\hub"
set "HF_ENDPOINT=https://hf-mirror.com"
set "HF_HUB_DISABLE_XET=1"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"

rem ---- 挂载内置 CUDA 运行库（不用另装 CUDA Toolkit / cuDNN）----
set "NVLIBS="
for /d %%D in ("%ROOT%runtime\python\Lib\site-packages\nvidia\*") do (
    if exist "%%D\bin" set "NVLIBS=!NVLIBS!%%D\bin;"
)
if defined NVLIBS set "PATH=!NVLIBS!%PATH%"

rem ---- 内置 ffmpeg（浏览器音频解码要用，不用另装）----
if exist "%ROOT%bin\ffmpeg.exe" set "PATH=%ROOT%bin;!PATH!"

set "PATH=%ROOT%runtime\python;%ROOT%runtime\python\Scripts;%PATH%"

rem ---- 显卡提示（真正的设备检测在 Python 里做，失败会自动降级 CPU）----
set "GPUNAME="
where nvidia-smi >nul 2>&1
if not errorlevel 1 (
    for /f "usebackq delims=" %%G in (`nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul`) do (
        if not defined GPUNAME set "GPUNAME=%%G"
    )
)
if defined GPUNAME echo [i] 检测到显卡: !GPUNAME!
if not defined GPUNAME echo [i] 未检测到 NVIDIA 显卡，将使用 CPU
echo.

rem ---- 启动 ----
if exist "%MODEL_DIR%\model.bin" (
    echo [i] 模型: %MODEL%（本地，无需联网）
    echo.
    "%PY%" "%ROOT%tools\run_server.py" ^
        --model-path "%MODEL_DIR%" ^
        --backend faster-whisper ^
        --backend-policy localagreement ^
        --warmup-file "%ROOT%assets\warmup.wav" ^
        --host 127.0.0.1 --port %PORT% --language %LANGUAGE%
) else (
    echo [!] 本地没有模型: %MODEL_DIR%
    echo     改用 small，首次运行会自动联网下载（约 500MB）
    echo.
    "%PY%" "%ROOT%tools\run_server.py" ^
        --model small ^
        --backend faster-whisper ^
        --backend-policy localagreement ^
        --warmup-file "%ROOT%assets\warmup.wav" ^
        --host 127.0.0.1 --port %PORT% --language %LANGUAGE%
)

set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
    echo [x] 服务异常退出，退出码 %EXITCODE%
    echo     日志: %ROOT%logs\server.log
    echo     排错: 双击 check.bat 跑一遍环境自检
    echo.
    pause
) else (
    echo [i] 服务已停止。
    timeout /t 2 >nul
)
endlocal
