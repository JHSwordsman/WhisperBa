"""WhisperLiveKit 启动器（便携版）。

职责：
  1. 检测推理设备（NVIDIA GPU / CPU），GPU 不可用时自动降级
  2. 把命令行参数原样交给 WhisperLiveKit 官方 CLI
  3. 服务端口就绪后自动打开浏览器
  4. 不 fork 子进程，关闭控制台窗口即可结束服务
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser


class _Tee:
    """把 stdout/stderr 同时写到控制台和日志文件。"""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False


def _setup_log_file() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logdir = os.path.join(root, "logs")
    try:
        os.makedirs(logdir, exist_ok=True)
        fh = open(os.path.join(logdir, "server.log"), "w", encoding="utf-8", errors="replace")
    except OSError:
        return
    sys.stdout = _Tee(sys.__stdout__, fh)
    sys.stderr = _Tee(sys.__stderr__, fh)


def _detect_accel() -> tuple[str, str]:
    """返回 (设备, 说明)。GPU 不可用时自动降级到 CPU。"""
    if os.environ.get("WLK_FORCE_CPU") == "1":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        return "cpu", "已按 WLK_FORCE_CPU 强制使用 CPU"
    try:
        import ctranslate2

        n = ctranslate2.get_cuda_device_count()
        if n <= 0:
            return "cpu", "未检测到 CUDA 设备"
        # 真正试一次：能列出 compute types 才说明 cublas/cudnn 都加载成功
        ctranslate2.get_supported_compute_types("cuda")
        return "cuda", f"CUDA 可用（{n} 个设备，float16）"
    except Exception as exc:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        return "cpu", f"GPU 初始化失败，已降级到 CPU（{type(exc).__name__}: {exc}）"


def _get_arg(name: str, default: str) -> str:
    argv = sys.argv
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return default


def _open_browser(host: str, port: int) -> None:
    """等服务真的监听了再开浏览器，最多等 180 秒。"""
    if os.environ.get("WLK_NO_BROWSER") == "1":
        return
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{probe_host}:{port}/"
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            with socket.create_connection((probe_host, port), timeout=1.0):
                break
        except OSError:
            time.sleep(0.4)
    else:
        print("[!] 服务在 180 秒内没有监听端口，浏览器未自动打开。", flush=True)
        return
    time.sleep(1.0)  # 等首屏静态资源就绪
    print(f"[+] 正在打开浏览器：{url}", flush=True)
    try:
        webbrowser.open(url)
    except Exception as exc:  # pragma: no cover - 环境相关
        print(f"[!] 打开浏览器失败：{exc}，请手动访问 {url}", flush=True)


def main() -> None:
    _setup_log_file()

    host = _get_arg("--host", "127.0.0.1")
    try:
        port = int(_get_arg("--port", "8000"))
    except ValueError:
        port = 8000

    model_arg = _get_arg("--model-path", "") or _get_arg("--model", "small")
    accel, detail = _detect_accel()

    print("=" * 58, flush=True)
    print("  WhisperLiveKit 便携版", flush=True)
    print(f"  模型   : {model_arg}", flush=True)
    print(f"  推理   : {'GPU 加速' if accel == 'cuda' else 'CPU'}（{detail}）", flush=True)
    print(f"  地址   : http://127.0.0.1:{port}/", flush=True)
    print("  关闭本窗口即停止服务", flush=True)
    print("=" * 58, flush=True)

    # 让官方 banner 也显示真实设备（它默认只看 torch，CPU 版 torch 会误报）
    try:
        import whisperlivekit.cli as _cli

        _cli._gpu_info = lambda: (
            f"CUDA via CTranslate2 ({detail})" if accel == "cuda" else "CPU only"
        )
    except Exception:
        pass

    threading.Thread(target=_open_browser, args=(host, port), daemon=True).start()

    sys.argv = ["wlk"] + sys.argv[1:]
    from whisperlivekit.cli import main as wlk_main

    wlk_main()


if __name__ == "__main__":
    main()
