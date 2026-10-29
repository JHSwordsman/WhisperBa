"""实时转写引擎：音频采集（麦克风 / 系统播放声音）+ WebSocket 推流 + 去重。

对外只暴露 AudioEngine：start(source, language) / stop()，事件通过 on_event 回调抛给 UI。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
from difflib import SequenceMatcher

# 本机回环不走系统代理
for _k in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_k, "")
    if "127.0.0.1" not in _cur:
        os.environ[_k] = ",".join(filter(None, [_cur, "127.0.0.1,localhost"]))

import numpy as np  # noqa: E402
import soundcard as sc  # noqa: E402

SAMPLE_RATE = 16000
BLOCK_FRAMES = 1600  # 100ms


# ------------------------------------------------------------------ 去重
_PUNCT = re.compile(r"[\s，。、,.!?！？；;：:\"'“”‘’（）()\[\]【】]")


def _norm(text: str) -> str:
    return _PUNCT.sub("", text or "")


class Deduper:
    """行级去重：挡住重复句、被包含的半句，保留更完整的那句。"""

    def __init__(self, window: int = 6, threshold: float = 0.88):
        self.recent: list[str] = []
        self.window = window
        self.threshold = threshold

    def check(self, text: str) -> str:
        """返回 'new' / 'replace' / 'skip'。"""
        text = (text or "").strip()
        if not text:
            return "skip"
        norm = _norm(text)
        if not norm:
            return "skip"

        for prev in reversed(self.recent[-self.window:]):
            pnorm = _norm(prev)
            if not pnorm:
                continue
            if norm == pnorm:
                return "skip"
            if norm in pnorm:
                return "skip"          # 新的更短，旧的是它的超集
            if pnorm in norm:
                return "replace"       # 新的更完整，替换上一行
            if SequenceMatcher(None, norm, pnorm).ratio() >= self.threshold:
                return "skip"
        return "new"

    def remember(self, text: str, action: str) -> None:
        if action == "replace" and self.recent:
            self.recent[-1] = text
        elif action == "new":
            self.recent.append(text)
            if len(self.recent) > self.window * 3:
                del self.recent[: -self.window * 2]

    def reset(self) -> None:
        self.recent.clear()


# ------------------------------------------------------------------ 设备
def _init_com() -> None:
    """WASAPI 走 COM，非主线程必须先初始化，否则可能直接崩掉进程。"""
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0x0)  # COINIT_MULTITHREADED
    except Exception:
        pass


def list_sources() -> dict:
    speakers = [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]
    mics = [m for m in sc.all_microphones(include_loopback=False)]
    return {"system": speakers, "mic": mics}


def _resolve(source: str, device_name: str | None):
    if source == "mic":
        if device_name:
            for m in sc.all_microphones(include_loopback=False):
                if device_name.lower() in m.name.lower():
                    return m
        mic = sc.default_microphone()
        if mic is None:
            raise RuntimeError("系统没有可用的麦克风")
        return mic

    # 系统播放声音：对默认扬声器做环回
    if device_name:
        for m in sc.all_microphones(include_loopback=True):
            if device_name.lower() in m.name.lower():
                return m
    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("系统没有默认播放设备")
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


# ------------------------------------------------------------------ 引擎
class AudioEngine:
    """后台线程里跑 asyncio：采集 -> ws 推流 -> 收字幕。"""

    def __init__(self, port: int = 8000, on_event=None):
        self.port = port
        self.on_event = on_event or (lambda ev: None)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self.running = False
        self.deduper = Deduper()

    # ---- 事件
    def _emit(self, **ev) -> None:
        try:
            self.on_event(ev)
        except Exception:
            pass

    # ---- 生命周期
    def start(self, source: str = "system", language: str = "zh", device: str | None = None) -> None:
        if self.running:
            return
        self._stop.clear()
        self.deduper.reset()
        self.running = True
        self._thread = threading.Thread(
            target=self._thread_main, args=(source, language, device), daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._stop.set()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(lambda: None)
        self._emit(type="status", text="已停止")

    # ---- 线程主体
    def _thread_main(self, source: str, language: str, device: str | None) -> None:
        _init_com()
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run(source, language, device))
        except Exception as exc:  # 兜底，避免线程静默死掉
            self._emit(type="error", text=f"{type(exc).__name__}: {exc}")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None
            self.running = False

    async def _run(self, source: str, language: str, device: str | None) -> None:
        try:
            dev = _resolve(source, device)
        except Exception as exc:
            self._emit(type="error", text=f"打不开音频设备：{exc}")
            return

        label = "麦克风" if source == "mic" else "系统声音"
        self._emit(type="status", text=f"{label}：{dev.name}")

        audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=300)
        loop = asyncio.get_running_loop()
        stats = {"rms": 0.0}

        def push(pcm: bytes) -> None:
            try:
                loop.call_soon_threadsafe(audio_q.put_nowait, pcm)
            except RuntimeError:
                pass

        worker = threading.Thread(
            target=self._capture, args=(dev, push, stats), daemon=True
        )
        worker.start()

        query = [
            "encoding=linear16",
            f"sample_rate={SAMPLE_RATE}",
            "channels=1",
            "interim_results=true",
        ]
        if language and language != "auto":
            query.append(f"language={language}")
        url = f"ws://127.0.0.1:{self.port}/v1/listen?" + "&".join(query)

        try:
            import websockets
        except ImportError:
            self._emit(type="error", text="缺少 websockets")
            return

        try:
            async with websockets.connect(
                url, max_size=None, ping_interval=20, proxy=None
            ) as ws:
                self._emit(type="status", text=f"{label}：{dev.name}（已连接）")

                async def sender() -> None:
                    while not self._stop.is_set():
                        pcm = await audio_q.get()
                        await ws.send(pcm)

                async def receiver() -> None:
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        try:
                            msg = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if msg.get("type") != "Results":
                            continue
                        alts = msg.get("channel", {}).get("alternatives") or [{}]
                        text = (alts[0].get("transcript") or "").strip()
                        if msg.get("is_final"):
                            if not text:
                                self._emit(type="interim", text="")
                                continue
                            action = self.deduper.check(text)
                            if action == "skip":
                                self._emit(type="interim", text="")
                                continue
                            self.deduper.remember(text, action)
                            self._emit(type="final", text=text, action=action)
                            self._emit(type="interim", text="")
                        else:
                            self._emit(type="interim", text=text)

                async def meter() -> None:
                    while not self._stop.is_set():
                        db = 20 * np.log10(stats["rms"] + 1e-9)
                        self._emit(type="level", db=db)
                        await asyncio.sleep(0.12)

                tasks = [
                    asyncio.create_task(sender()),
                    asyncio.create_task(receiver()),
                    asyncio.create_task(meter()),
                ]
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )
                for t in pending:
                    t.cancel()
                for t in done:
                    exc = t.exception()
                    if exc and not self._stop.is_set():
                        self._emit(type="error", text=f"连接中断：{exc}")
        except Exception as exc:
            if not self._stop.is_set():
                self._emit(
                    type="error",
                    text=f"连不上服务（{type(exc).__name__}）：先启动 start.bat",
                )
        finally:
            self._stop.set()

    # ---- 采集线程
    def _capture(self, device, push, stats) -> None:
        _init_com()
        try:
            with device.recorder(
                samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_FRAMES
            ) as rec:
                while not self._stop.is_set():
                    data = rec.record(numframes=BLOCK_FRAMES)
                    mono = data[:, 0] if data.ndim > 1 else data
                    if mono.size:
                        stats["rms"] = float(
                            np.sqrt(np.mean(np.square(mono.astype(np.float64))))
                        )
                    push(
                        (np.clip(mono, -1.0, 1.0) * 32767.0)
                        .astype(np.int16)
                        .tobytes()
                    )
        except Exception as exc:
            if not self._stop.is_set():
                self._emit(type="error", text=f"采集中断：{exc}")
