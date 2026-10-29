"""WhisperBar 启动器（打包成 WhisperBar.exe）。

双击后：
  - 环境齐全  -> 直接拉起悬浮字幕条（无控制台窗口）
  - 环境缺失  -> 弹出安装向导，点一下就自动装（复用 install.bat）
  - 已经在跑  -> 把已有窗口叫到前台，不重复启动
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import tkinter as tk

WINDOW_TITLE = "WhisperBar 浮声 · 实时字幕"
CREATE_NO_WINDOW = 0x08000000


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = base_dir()


def hide_console() -> None:
    """打包成 console exe（Defender 会删 runw.exe，只能用 run.exe），
    启动后立刻把黑窗口藏起来，用户几乎看不到。"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def log_exc() -> None:
    try:
        import traceback

        logdir = ROOT / "logs"
        logdir.mkdir(exist_ok=True)
        (logdir / "launcher.log").write_text(traceback.format_exc(), encoding="utf-8")
    except Exception:
        pass
PYEXE = ROOT / "runtime" / "python" / "python.exe"
PYW = ROOT / "runtime" / "python" / "pythonw.exe"
APP = ROOT / "tools" / "float_app.py"
MODELS = ROOT / "models"


def env_ok() -> bool:
    return PYEXE.exists() and APP.exists()


def models_ok() -> bool:
    for name in ("faster-whisper-large-v3-turbo", "faster-whisper-small"):
        if (MODELS / name / "model.bin").exists():
            return True
    return False


def build_env() -> dict:
    env = os.environ.copy()
    # PyInstaller 自己会设 TCL_LIBRARY/TK_LIBRARY 指向临时解包目录 _MEIxxxx，
    # 那个目录在 launcher 退出后就被删了。不清掉的话，子进程里的 tkinter 会崩。
    for key in list(env.keys()):
        if key.startswith("_PYI") or key.startswith("_MEI"):
            env.pop(key, None)
    for key in ("TCL_LIBRARY", "TK_LIBRARY", "TCLLIBPATH", "PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    nv = [str(p) for p in (ROOT / "runtime" / "python" / "Lib" / "site-packages" / "nvidia").glob("*/bin")]
    env["PATH"] = os.pathsep.join(
        nv + [str(ROOT / "bin"), str(PYEXE.parent), env.get("PATH", "")]
    )
    return env


def launch_app() -> None:
    exe = PYW if PYW.exists() else PYEXE
    subprocess.Popen(
        [str(exe), str(APP)],
        cwd=str(ROOT),
        env=build_env(),
        creationflags=CREATE_NO_WINDOW,
    )


def focus_existing() -> bool:
    try:
        hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- 安装向导
class SetupWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("WhisperBar 浮声 · 安装")
        self.root.configure(bg="#ffffff")
        self.root.resizable(False, False)
        w, h = 460, 230
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.root.attributes("-topmost", True)

        tk.Label(self.root, text="WhisperBar 浮声", bg="#ffffff", fg="#1f2937",
                 font=("Microsoft YaHei UI", 16, "bold")).pack(pady=(22, 2))
        tk.Label(self.root, text="Windows 实时悬浮字幕条", bg="#ffffff", fg="#8b95a5",
                 font=("Microsoft YaHei UI", 9)).pack()

        self.status = tk.Label(
            self.root, text="首次使用需要先装运行环境（约 5 GB，10~30 分钟）",
            bg="#ffffff", fg="#4b5563", font=("Microsoft YaHei UI", 9),
            wraplength=400, justify="center",
        )
        self.status.pack(pady=(18, 10))

        self.btn = tk.Button(
            self.root, text="开始安装", font=("Microsoft YaHei UI", 10, "bold"),
            bg="#2563eb", fg="white", activebackground="#1d4ed8", activeforeground="white",
            relief="flat", padx=28, pady=8, cursor="hand2", command=self.start_install,
        )
        self.btn.pack()

        tk.Label(self.root, text="装完会自动启动，下次双击就直接用了", bg="#ffffff",
                 fg="#a0aab8", font=("Microsoft YaHei UI", 8)).pack(pady=(12, 0))

    def start_install(self) -> None:
        self.btn.configure(state="disabled", text="安装中…")
        self.status.configure(text="正在安装，请在弹出的窗口里等它跑完（别关那个窗口）")
        install_bat = ROOT / "install.bat"
        if not install_bat.exists():
            self.status.configure(text="找不到 install.bat，这个包可能不完整")
            return
        subprocess.Popen(
            ["cmd", "/c", str(install_bat)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self) -> None:
        deadline = time.time() + 3600
        while time.time() < deadline:
            time.sleep(3)
            if env_ok() and models_ok():
                self.root.after(0, self._done)
                return
        self.root.after(0, lambda: self.status.configure(text="等太久了，检查一下安装窗口里的提示"))

    def _done(self) -> None:
        self.status.configure(text="装好了，正在启动…")
        self.root.after(800, self._launch_and_close)

    def _launch_and_close(self) -> None:
        launch_app()
        self.root.after(600, self.root.destroy)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if not APP.exists():
        ctypes.windll.user32.MessageBoxW(
            None, "这个包不完整，找不到 tools\\float_app.py", "WhisperBar", 0x10
        )
        return 1

    if focus_existing():
        return 0

    if env_ok() and models_ok():
        launch_app()
        return 0

    SetupWindow().run()
    return 0


if __name__ == "__main__":
    hide_console()
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        log_exc()
        ctypes.windll.user32.MessageBoxW(
            None, "启动失败，详情见 logs\\launcher.log", "WhisperBar", 0x10
        )
        sys.exit(1)
