"""WhisperLiveKit 悬浮字幕条（Windows）。

三个能力，一个窗口：
  1. 录音（麦克风）
  2. 采集电脑播放的声音（WASAPI 环回）
  3. 截图 OCR —— 抓画面里的文字，和同一时段的转写一起存下来

默认是「折叠」状态：只有一条半透明的单行字幕浮在屏幕底部，鼠标移上去才出现按钮；
点一下（或点「展开」）变成完整面板，能看全部历史、截图，也能一键存成 Markdown。

启动：双击 float.bat
"""
from __future__ import annotations

import os
import queue
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import tkinter as tk

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from PIL import Image, ImageGrab, ImageTk  # noqa: E402

import ocr_tool  # noqa: E402
from asr_engine import AudioEngine  # noqa: E402

# ------------------------------------------------------------------ 外观
FONT = "Microsoft YaHei UI"
BG = "#ffffff"
BORDER = "#d5dbe3"
HEADER = "#f5f7fa"
TEXT = "#1f2937"
MUTED = "#8b95a5"
ACCENT = "#2563eb"
GREEN = "#16a34a"
RED = "#dc2626"
CHIP = "#e8edf4"
HOVER = "#dbe3ee"

DARK = "#15181d"          # 折叠条底色
DARK_TEXT = "#ffffff"
DARK_MUTED = "#9aa4b2"
DARK_CHIP = "#2a2f37"

W_EXPAND = 900
H_EXPAND = 320
H_COLLAPSE = 52
ALPHA_COLLAPSE = 0.90


class FloatingApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("WhisperBar 浮声 · 实时字幕")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=DARK)
        self.root.pack_propagate(False)

        self.events: queue.Queue = queue.Queue()
        self.engine = AudioEngine(port=8000, on_event=lambda ev: self.events.put(ev))
        self.timeline: list[dict] = []
        self.collapsed = True
        self.source = "system"
        self.language = "zh"
        self.server_proc: subprocess.Popen | None = None
        self.server_ready = False
        self._drag: tuple[int, int] | None = None
        self._moved = False
        self._level_db = -100.0
        self._shot_busy = False
        self._photos: list = []
        self._last_line = ""
        self._status_text = "正在启动…"
        # 句子聚合：短片段先攒着，攒成一句（或满 5 秒）再落一行
        self._pending = ""
        self._pending_since = 0.0
        self._last_chunk = ""
        self._flush_timer = None
        self.MAX_SENTENCE_SEC = 5.0
        self.SILENCE_FLUSH_MS = 1200

        self._build()
        self._place()
        self.root.attributes("-alpha", ALPHA_COLLAPSE)
        self._start_server_watch()

        self.root.after(60, self._poll)
        self.root.after(150, self._animate_level)
        # 启动时先亮一下按钮，让人知道这里有东西
        self.root.after(1200, self._c_enter)
        self.root.after(5200, self._c_leave)

    # ------------------------------------------------------------ 结构
    def _build(self) -> None:
        self.outer = tk.Frame(self.root, bg=DARK)
        self.outer.pack(fill="both", expand=True)

        self._build_collapsed()
        self._build_panel()
        self.cbar.pack(fill="both", expand=True)

    # ---------------- 折叠条（默认显示）
    def _build_collapsed(self) -> None:
        self.cbar = tk.Frame(self.outer, bg=DARK, height=H_COLLAPSE)
        self.cbar.grid_columnconfigure(0, weight=1)
        self.cbar.grid_rowconfigure(0, weight=1)

        self.cdot = tk.Canvas(self.cbar, width=10, height=10, bg=DARK, highlightthickness=0)
        self.cdot.grid(row=0, column=0, sticky="w", padx=(14, 0))
        self.cdot_id = self.cdot.create_oval(1, 1, 9, 9, fill=DARK_MUTED, outline="")

        self.clabel = tk.Label(
            self.cbar, text="正在启动转写服务…", bg=DARK, fg=DARK_MUTED,
            font=(FONT, 13), anchor="w", justify="left",
        )
        self.clabel.grid(row=0, column=0, sticky="ew", padx=(34, 10))

        self.cbtns = tk.Frame(self.cbar, bg=DARK)
        self.cbtns.grid(row=0, column=1, sticky="e", padx=(0, 10))
        self.cbtns.grid_remove()

        self._cbtn("×", self.quit, fg="#ff8b8b")
        self._cbtn("展开", self.toggle_collapse)
        self._cbtn("保存", self.export_md)
        self._cbtn("截图", self.take_screenshot)
        self.csrc = self._cbtn("系统声音", self._toggle_source, fg="#9ec5ff")
        self.crec = self._cbtn("▶ 开始", self.toggle_record, fg="#9be7b4")

        for w in (self.cbar, self.clabel, self.cdot):
            w.bind("<Enter>", self._c_enter)
            w.bind("<Leave>", self._c_leave)
            w.bind("<Button-1>", self._c_press)
            w.bind("<B1-Motion>", self._c_move)
            w.bind("<ButtonRelease-1>", self._c_release)

    def _cbtn(self, text, cmd, fg=DARK_TEXT):
        lb = tk.Label(self.cbtns, text=text, bg=DARK_CHIP, fg=fg, font=(FONT, 9),
                      padx=9, pady=2, cursor="hand2")
        lb.pack(side="right", padx=3)
        lb.bind("<Button-1>", lambda e, c=cmd: c())
        lb.bind("<Enter>", lambda e: lb.configure(bg="#3a4048"))
        lb.bind("<Leave>", lambda e: lb.configure(bg=DARK_CHIP))
        return lb

    # ---------------- 展开面板
    def _build_panel(self) -> None:
        self.panel = tk.Frame(self.outer, bg=BG)

        self.header = tk.Frame(self.panel, bg=HEADER, height=40)
        self.header.pack(fill="x", side="top")
        self.header.pack_propagate(False)
        self.header.bind("<Button-1>", self._drag_start)
        self.header.bind("<B1-Motion>", self._drag_move)
        self.header.bind("<Double-Button-1>", lambda e: self.toggle_collapse())

        self.dot = tk.Canvas(self.header, width=14, height=14, bg=HEADER, highlightthickness=0)
        self.dot.pack(side="left", padx=(12, 6))
        self.dot_id = self.dot.create_oval(3, 3, 12, 12, fill=MUTED, outline="")
        tk.Label(self.header, text="字幕", bg=HEADER, fg=TEXT, font=(FONT, 10, "bold")).pack(side="left")

        self.level = tk.Canvas(self.header, width=118, height=10, bg=HEADER, highlightthickness=0)
        self.level.pack(side="left", padx=(12, 4))
        self.level.create_rectangle(0, 2, 118, 8, fill="#e3e8ef", outline="")
        self.level_bar = self.level.create_rectangle(0, 2, 1, 8, fill=GREEN, outline="")
        self.level_txt = tk.Label(self.header, text="--", bg=HEADER, fg=MUTED,
                                  font=(FONT, 8), width=6, anchor="w")
        self.level_txt.pack(side="left")

        self._btn(self.header, "×", self.quit, fg=RED, pad=(8, 8))
        self._btn(self.header, "收起", self.toggle_collapse, pad=(6, 2))
        self._btn(self.header, "保存 MD", self.export_md, pad=(6, 2))
        self._btn(self.header, "截图识别", self.take_screenshot, pad=(6, 2))

        sw = tk.Frame(self.header, bg=HEADER)
        sw.pack(side="right", padx=(0, 10))
        self.src_labels: dict[str, tk.Label] = {}
        for key, text in (("mic", "麦克风"), ("system", "系统声音")):
            lb = tk.Label(sw, text=text, font=(FONT, 9), padx=10, pady=3, cursor="hand2",
                          bg=CHIP, fg=TEXT)
            lb.pack(side="left", padx=2)
            lb.bind("<Button-1>", lambda e, k=key: self.set_source(k))
            self.src_labels[key] = lb

        self.rec_btn = tk.Label(self.header, text="▶ 开始", font=(FONT, 9, "bold"),
                                padx=12, pady=3, bg=ACCENT, fg="white", cursor="hand2")
        self.rec_btn.pack(side="right", padx=(0, 8))
        self.rec_btn.bind("<Button-1>", lambda e: self.toggle_record())

        self.status = tk.Label(self.panel, text="正在检查转写服务…", bg=BG, fg=MUTED,
                               font=(FONT, 8), anchor="w")
        self.status.pack(fill="x", side="bottom", padx=12, pady=(0, 4))

        self.wrap = tk.Frame(self.panel, bg=BG)
        self.wrap.pack(fill="both", expand=True, padx=10, pady=(6, 2))

        self.text = tk.Text(self.wrap, bg=BG, fg=TEXT, font=(FONT, 10), wrap="word",
                            borderwidth=0, highlightthickness=0, padx=2, pady=2,
                            spacing1=2, spacing3=4)
        self.text.pack(fill="both", expand=True)
        self.text.tag_configure("ts", foreground=MUTED, font=(FONT, 8))
        self.text.tag_configure("interim", foreground=MUTED)
        self.text.tag_configure("ocr", foreground="#6d28d9")
        self.text.tag_configure("shot", foreground="#6d28d9", font=(FONT, 9, "italic"))
        self.text.bind("<Key>", self._on_text_key)

        self._paint_source()

    def _btn(self, parent, text, cmd, fg=TEXT, pad=(6, 2)):
        lb = tk.Label(parent, text=text, bg=CHIP, fg=fg, font=(FONT, 9),
                      padx=10, pady=3, cursor="hand2")
        lb.pack(side="right", padx=pad)
        lb.bind("<Button-1>", lambda e: cmd())
        lb.bind("<Enter>", lambda e: lb.configure(bg=HOVER))
        lb.bind("<Leave>", lambda e: lb.configure(bg=CHIP))
        return lb

    def _place(self) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        h = H_COLLAPSE if self.collapsed else H_EXPAND
        self.root.geometry(f"{W_EXPAND}x{h}+{(sw - W_EXPAND) // 2}+{sh - h - 44}")

    # ------------------------------------------------------------ 折叠交互
    def _c_enter(self, _e=None) -> None:
        self.cbtns.grid()

    def _c_leave(self, _e=None) -> None:
        self.root.after(90, self._maybe_hide_btns)

    def _maybe_hide_btns(self) -> None:
        if not self.collapsed:
            return
        x, y = self.root.winfo_pointerxy()
        wx, wy = self.root.winfo_x(), self.root.winfo_y()
        if not (wx <= x <= wx + self.root.winfo_width() and wy <= y <= wy + self.root.winfo_height()):
            self.cbtns.grid_remove()

    def _c_press(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())
        self._moved = False

    def _c_move(self, e):
        if self._drag:
            self._moved = True
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _c_release(self, _e):
        if not self._moved:
            self.toggle_collapse()
        self._drag = None

    # ------------------------------------------------------------ 展开态拖动
    def _drag_start(self, e):
        self._drag = (e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y())

    def _drag_move(self, e):
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _on_text_key(self, e):
        if e.state & 0x4 and e.keysym.lower() in ("c", "a"):
            return None
        return "break"

    def toggle_collapse(self) -> None:
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.panel.pack_forget()
            self.outer.configure(bg=DARK)
            self.cbar.pack(fill="both", expand=True)
            self.root.attributes("-alpha", ALPHA_COLLAPSE)
            self.cbtns.grid_remove()
        else:
            self.cbar.pack_forget()
            self.outer.configure(bg=BORDER)
            self.panel.pack(fill="both", expand=True, padx=1, pady=1)
            self.root.attributes("-alpha", 1.0)
            self.text.see("end")
        self._place()

    # ------------------------------------------------------------ 服务
    def _port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 8000), timeout=0.6):
                return True
        except OSError:
            return False

    def _start_server_watch(self) -> None:
        threading.Thread(target=self._watch_server, daemon=True).start()

    def _watch_server(self) -> None:
        if not self._port_open():
            self._set_status("转写服务未启动，正在拉起（首次 20~40 秒）…")
            self._launch_server()
        else:
            self._set_status("转写服务已在运行，等待模型就绪…")

        deadline = time.time() + 180
        while time.time() < deadline:
            if self._health_ok():
                self.server_ready = True
                self._set_status("就绪 · 悬停看按钮，点一下展开")
                self._dot(GREEN)
                if os.environ.get("WLK_AUTOSTART") == "1":
                    self.root.after(300, self.toggle_record)
                return
            time.sleep(1.5)
        self._set_status("服务启动超时，请看 logs\\server.log", error=True)
        self._dot(RED)

    def _launch_server(self) -> None:
        py = ROOT / "runtime" / "python" / "python.exe"
        model_dir = ROOT / "models" / "faster-whisper-large-v3-turbo"
        if not model_dir.exists():
            model_dir = ROOT / "models" / "faster-whisper-small"
        cmd = [
            str(py), str(ROOT / "tools" / "run_server.py"),
            "--model-path", str(model_dir),
            "--backend", "faster-whisper",
            "--backend-policy", "localagreement",
            "--warmup-file", str(ROOT / "assets" / "warmup.wav"),
            "--host", "127.0.0.1", "--port", "8000", "--language", "auto",
        ]
        env = os.environ.copy()
        env["WLK_NO_BROWSER"] = "1"
        env["PYTHONPATH"] = ""
        env["PYTHONHOME"] = ""
        env["NO_PROXY"] = "127.0.0.1,localhost"
        env["no_proxy"] = "127.0.0.1,localhost"
        nv = [str(p) for p in (ROOT / "runtime" / "python" / "Lib" / "site-packages" / "nvidia").glob("*/bin")]
        env["PATH"] = os.pathsep.join(nv + [str(ROOT / "bin"), str(py.parent), env.get("PATH", "")])
        try:
            self.server_proc = subprocess.Popen(
                cmd, cwd=str(ROOT), env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            self._set_status(f"启动服务失败：{exc}", error=True)

    def _health_ok(self) -> bool:
        import urllib.request

        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open("http://127.0.0.1:8000/health", timeout=2) as r:
                body = r.read().decode("utf-8", "ignore").replace(" ", "")
                return r.status == 200 and '"ready":true' in body
        except Exception:
            return False

    # ------------------------------------------------------------ 录制
    def _toggle_source(self) -> None:
        """折叠条上的快捷切换：系统声音 <-> 麦克风。录音中会顺带重连。"""
        self.set_source("mic" if self.source == "system" else "system")

    def set_source(self, key: str) -> None:
        was_running = self.engine.running
        if was_running:
            self.engine.stop()
            self.rec_btn.configure(text="▶ 开始", bg=ACCENT)
            self.crec.configure(text="▶ 开始")
            self._dot(MUTED)
        self.source = key
        self._paint_source()
        label = "麦克风" if key == "mic" else "系统声音"
        self.csrc.configure(text=label)
        self._set_status(f"音源已切到「{label}」" + ("，正在重连…" if was_running else "，点 ▶ 开始"))
        if was_running:
            self.root.after(600, self.toggle_record)

    def _paint_source(self) -> None:
        for key, lb in self.src_labels.items():
            if key == self.source:
                lb.configure(bg=ACCENT, fg="white")
            else:
                lb.configure(bg=CHIP, fg=TEXT)

    def toggle_record(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self._flush_sentence()
            self.rec_btn.configure(text="▶ 开始", bg=ACCENT)
            self.crec.configure(text="▶ 开始")
            self._dot(MUTED)
            self._set_status("已停止")
            return
        if not self.server_ready:
            self._set_status("服务还没就绪，稍等一下")
            return
        self.engine.start(source=self.source, language=self.language)
        self.rec_btn.configure(text="■ 停止", bg=RED)
        self.crec.configure(text="■ 停止")
        self._dot(GREEN)
        self._set_status("录音中…")

    # ------------------------------------------------------------ 事件
    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(60, self._poll)

    def _handle(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "level":
            self._level_db = ev.get("db", -100.0)
        elif kind == "status":
            self._set_status(ev.get("text", ""))
        elif kind == "error":
            self._set_status(ev.get("text", ""), error=True)
            self._dot(RED)
            self.rec_btn.configure(text="▶ 开始", bg=ACCENT)
            self.crec.configure(text="▶ 开始")
        elif kind == "interim":
            self._show_interim(ev.get("text", ""))
        elif kind == "final":
            self._add_final(ev.get("text", ""), ev.get("action", "new"))
        elif kind == "ocr_done":
            self._on_ocr_done(ev)

    def _animate_level(self) -> None:
        db = self._level_db
        ratio = max(0.0, min(1.0, (db + 60) / 60))
        width = max(1, int(118 * ratio))
        color = GREEN if db > -35 else ("#f59e0b" if db > -50 else "#cbd5e1")
        self.level.coords(self.level_bar, 0, 2, width, 8)
        self.level.itemconfigure(self.level_bar, fill=color)
        self.level_txt.configure(text=f"{db:.0f}dB" if db > -99 else "--",
                                 fg=MUTED if db < -50 else TEXT)
        self.root.after(110, self._animate_level)

    def _set_status(self, text: str, error: bool = False) -> None:
        self._status_text = text
        self.status.configure(text=text, fg=RED if error else MUTED)
        if self.collapsed and not self._last_line:
            self.clabel.configure(text=text, fg=("#ff8b8b" if error else DARK_MUTED))

    def _dot(self, color: str) -> None:
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.cdot.itemconfigure(self.cdot_id, fill=color if color != MUTED else DARK_MUTED)

    # ------------------------------------------------------------ 文本
    def _update_clabel(self, text: str, interim: bool = False) -> None:
        """折叠条只显示一行：取最新一句，超长截尾。"""
        t = " ".join((text or "").split())
        if not t:
            return
        if len(t) > 46:
            t = "…" + t[-45:]
        self._last_line = t
        self.clabel.configure(text=t, fg=(DARK_MUTED if interim else DARK_TEXT))

    def _show_interim(self, text: str) -> None:
        ranges = self.text.tag_ranges("interim")
        if ranges:
            self.text.delete(ranges[0], ranges[1])
        if text:
            self.text.insert("end", text, "interim")
        self.text.see("end")
        combined = (self._pending + (text or "")).strip()
        if combined:
            self._update_clabel(combined, interim=bool(text))

    # 句子聚合：碎片先攒成一句，避免一行只有两三个字
    END_PUNCT = "。！？!?；;…"
    MAX_SENTENCE_CHARS = 110

    def _add_final(self, text: str, action: str) -> None:
        ranges = self.text.tag_ranges("interim")
        if ranges:
            self.text.delete(ranges[0], ranges[1])

        if action == "replace" and self._last_chunk and self._pending.endswith(self._last_chunk):
            self._pending = self._pending[: -len(self._last_chunk)] + text
        else:
            self._pending += text
        self._last_chunk = text
        if not self._pending_since:
            self._pending_since = time.time()

        elapsed = time.time() - self._pending_since
        tail = self._pending.rstrip()[-1:]
        if (
            tail in self.END_PUNCT
            or elapsed >= self.MAX_SENTENCE_SEC
            or len(self._pending) >= self.MAX_SENTENCE_CHARS
        ):
            self._flush_sentence()
        else:
            self._schedule_flush()
            self._update_clabel(self._pending, interim=True)

    def _schedule_flush(self) -> None:
        if self._flush_timer:
            try:
                self.root.after_cancel(self._flush_timer)
            except Exception:
                pass
        self._flush_timer = self.root.after(self.SILENCE_FLUSH_MS, self._flush_sentence)

    def _flush_sentence(self) -> None:
        if self._flush_timer:
            try:
                self.root.after_cancel(self._flush_timer)
            except Exception:
                pass
            self._flush_timer = None

        text = self._pending.strip()
        self._pending = ""
        self._last_chunk = ""
        self._pending_since = 0.0
        if not text:
            return

        ranges = self.text.tag_ranges("interim")
        if ranges:
            self.text.delete(ranges[0], ranges[1])

        ts = datetime.now()
        self.timeline.append({"ts": ts, "kind": "speech", "text": text, "image": None})
        self.text.insert("end", f"{ts:%H:%M:%S}  ", "ts")
        self.text.insert("end", text + "\n")
        self.text.see("end")
        self._update_clabel(text)

    # ------------------------------------------------------------ 截图存档
    def take_screenshot(self) -> None:
        if self._shot_busy:
            return
        self.root.withdraw()
        self.root.after(220, self._open_snipper)

    def _open_snipper(self) -> None:
        from PIL import ImageEnhance

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()

        # 先把屏幕拍下来、压暗当背景，这样框选时看得见底下的画面
        try:
            full = ImageGrab.grab(all_screens=True)
            if sw:
                self._scale_cache = max(1.0, full.size[0] / sw)
            dark = ImageEnhance.Brightness(full).enhance(0.42).resize((sw, sh), Image.LANCZOS)
            bg_photo = ImageTk.PhotoImage(dark)
        except Exception:
            full, bg_photo = None, None

        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.geometry(f"{sw}x{sh}+0+0")
        top.attributes("-topmost", True)
        top.configure(bg="#0d1117")
        top.focus_force()

        canvas = tk.Canvas(top, bg="#0d1117", highlightthickness=0, cursor="cross",
                           width=sw, height=sh)
        canvas.pack(fill="both", expand=True)
        if bg_photo is not None:
            canvas.create_image(0, 0, anchor="nw", image=bg_photo)
            canvas.image = bg_photo  # 防 GC

        canvas.create_text(
            sw // 2, 70, text="拖拽框住要保存的画面　·　Esc 取消",
            fill="#ffffff", font=(FONT, 14, "bold"),
        )

        st = {"x": 0, "y": 0, "rect": None, "size": None}

        def on_press(e):
            st["x"], st["y"] = e.x, e.y
            for key in ("rect", "size"):
                if st.get(key):
                    canvas.delete(st[key])
                    st[key] = None
            st["rect"] = canvas.create_rectangle(
                e.x, e.y, e.x, e.y, outline="#ff3b30", width=2,
            )

        def on_drag(e):
            if not st["rect"]:
                return
            canvas.coords(st["rect"], st["x"], st["y"], e.x, e.y)
            if st["size"]:
                canvas.delete(st["size"])
            st["size"] = canvas.create_text(
                e.x + 10, e.y + 18, anchor="w",
                text=f"{abs(e.x - st['x'])} × {abs(e.y - st['y'])}",
                fill="#ff8b8b", font=(FONT, 10, "bold"),
            )

        def finish(e):
            x1, y1, x2, y2 = st["x"], st["y"], e.x, e.y
            left, top_y = min(x1, x2), min(y1, y2)
            w, h = abs(x2 - x1), abs(y2 - y1)
            top.destroy()
            if w < 12 or h < 12:
                self.root.deiconify()
                self._set_status("选区太小，已取消")
                return
            # 窗口这会儿还藏着，先截图再显示，免得把自己也截进去
            self.root.after(120, lambda: self._save_shot(left, top_y, w, h))

        canvas.bind("<Button-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", finish)
        canvas.bind("<Escape>", lambda e: (top.destroy(), self.root.deiconify()))
        top.bind("<Escape>", lambda e: (top.destroy(), self.root.deiconify()))

    def _dpi_scale(self) -> float:
        """窗口坐标是逻辑像素，ImageGrab 要物理像素，这里算真实缩放比。

        tk 在非 DPI 感知进程里永远报告 96dpi，系统 API 也会被虚拟化，
        所以直接拿「截一张全屏的实际像素宽 / tk 认为的屏宽」来算，最实在。
        """
        cached = getattr(self, "_scale_cache", None)
        if cached:
            return cached
        try:
            phys_w = ImageGrab.grab(all_screens=True).size[0]
            logical_w = self.root.winfo_screenwidth()
            if phys_w and logical_w:
                self._scale_cache = max(1.0, phys_w / logical_w)
                return self._scale_cache
        except Exception:
            pass
        return 1.0

    def _save_shot(self, x: int, y: int, w: int, h: int) -> None:
        """把画面存下来，挂在当前时间点上，和字幕归到同一段。"""
        scale = self._dpi_scale()
        bbox = (
            int(x * scale), int(y * scale),
            int((x + w) * scale), int((y + h) * scale),
        )
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
        except Exception as exc:
            self.root.deiconify()
            self._set_status(f"截图失败：{exc}", error=True)
            return

        self.root.deiconify()  # 抓完立刻把字幕条放回来

        shots = ROOT / "shots"
        shots.mkdir(exist_ok=True)
        ts = datetime.now()
        name = f"{ts:%Y%m%d_%H%M%S}.png"
        img.save(shots / name)

        # 面板里直接显示缩略图
        thumb = img.copy()
        thumb.thumbnail((300, 300), Image.LANCZOS)
        photo = ImageTk.PhotoImage(thumb)
        self._photos.append(photo)

        idx = len(self.timeline)
        mark = f"shot_{idx}"
        self.text.insert("end", f"{ts:%H:%M:%S}  ", "ts")
        self.text.insert("end", "截图\n", "shot")
        self.text.image_create("end", image=photo, padx=2, pady=2)
        self.text.insert("end", "\n")
        self.text.mark_set(mark, "end-1c")
        self.text.mark_gravity(mark, "left")
        self.text.see("end")

        self.timeline.append({
            "ts": ts, "kind": "shot", "text": "", "image": f"shots/{name}", "mark": mark,
        })
        self._set_status(f"已截图存档 {name}（和这段字幕归在一起）")

        # 顺手识别图里的文字，有就贴在图片下面
        self._shot_busy = True
        threading.Thread(target=self._ocr_worker, args=(img, idx), daemon=True).start()

    def _ocr_worker(self, img: Image.Image, idx: int) -> None:
        text, err = ocr_tool.recognize(img, "zh-Hans")
        self.events.put({"type": "ocr_done", "text": text, "error": err, "idx": idx})

    def _on_ocr_done(self, ev: dict) -> None:
        self._shot_busy = False
        text = (ev.get("text") or "").strip()
        idx = ev.get("idx", -1)
        if not (0 <= idx < len(self.timeline)):
            return
        item = self.timeline[idx]
        if not text:
            return
        item["text"] = text
        try:
            self.text.insert(item["mark"], text + "\n", "ocr")
        except tk.TclError:
            pass
        self._set_status(f"截图已存档，顺带识别到 {len(text)} 字")
        self.text.see("end")

    # ------------------------------------------------------------ 导出
    def export_md(self) -> None:
        self._flush_sentence()
        items = [it for it in self.timeline if not it.get("pending")]
        if not items:
            self._set_status("还没有内容可以保存")
            return

        out_dir = ROOT / "output"
        att_dir = out_dir / "attachments"
        out_dir.mkdir(exist_ok=True)

        # 图片统一拷到 md 同级 attachments/，整个 output 目录可直接丢进 Obsidian vault
        for it in items:
            rel = it.get("image")
            if not rel:
                continue
            src = ROOT / rel
            if not src.exists():
                it["md_image"] = None
                continue
            att_dir.mkdir(exist_ok=True)
            dst = att_dir / src.name
            if not dst.exists():
                try:
                    import shutil

                    shutil.copy2(src, dst)
                except Exception:
                    it["md_image"] = None
                    continue
            it["md_image"] = f"attachments/{src.name}"

        path = out_dir / f"{datetime.now():%Y%m%d_%H%M%S}.md"

        blocks: list[list[dict]] = []
        for it in items:
            if blocks and (it["ts"] - blocks[-1][-1]["ts"]).total_seconds() <= 10:
                blocks[-1].append(it)
            else:
                blocks.append([it])

        src = "系统播放声音" if self.source == "system" else "麦克风"
        lines = [
            f"# 转写记录 · {datetime.now():%Y-%m-%d %H:%M}",
            "",
            f"> 音源：{src}　·　模型：large-v3-turbo　·　共 {len(items)} 条",
            "",
        ]
        for block in blocks:
            lines.append(f"## {block[0]['ts']:%H:%M:%S}")
            lines.append("")
            for it in block:
                if it.get("md_image"):
                    lines.append(f"![截图]({it['md_image']})")
                    lines.append("")
                if it.get("text"):
                    lines.append(it["text"])
                    lines.append("")

        try:
            path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:
            self._set_status(f"保存失败：{exc}", error=True)
            return
        self._set_status(f"已保存：output\\{path.name}")

    # ------------------------------------------------------------ 退出
    def quit(self) -> None:
        self.engine.stop()
        if self.server_proc and self.server_proc.poll() is None:
            try:
                self.server_proc.terminate()
            except Exception:
                pass
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    try:
        FloatingApp().run()
    except Exception:
        import traceback

        log = ROOT / "logs" / "float_error.log"
        log.parent.mkdir(exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        raise
