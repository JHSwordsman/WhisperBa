"""采集「电脑正在播放的声音」并实时转写（WASAPI 环回，不需要虚拟声卡）。

原理：Windows 的 WASAPI 支持对任意播放设备做 loopback 采集，
      所以直接把扬声器输出的音频抓下来，转成 16k 单声道 PCM 推给 WhisperLiveKit。

用法（先启动 start.bat，再运行本脚本）：
    python tools\\capture_system_audio.py                 # 中文，默认扬声器
    python tools\\capture_system_audio.py --language auto # 自动识别语种
    python tools\\capture_system_audio.py --device "扬声器 (Realtek(R) Audio)"
    python tools\\capture_system_audio.py --list          # 列出可采集的设备
    python tools\\capture_system_audio.py --save out.txt  # 同时保存字幕

按 Ctrl+C 停止。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# 本机回环不要走系统代理（否则 ws 连接会被代理挡掉）
for _k in ("NO_PROXY", "no_proxy"):
    _cur = os.environ.get(_k, "")
    if "127.0.0.1" not in _cur:
        os.environ[_k] = ",".join(filter(None, [_cur, "127.0.0.1,localhost"]))

import numpy as np
import soundcard as sc

SAMPLE_RATE = 16000
BLOCK_FRAMES = 1600  # 100 ms

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 设备
def list_loopback_devices() -> list:
    return [m for m in sc.all_microphones(include_loopback=True) if m.isloopback]


def resolve_device(name: str | None):
    """返回用于采集的 loopback 设备。"""
    if name:
        for mic in sc.all_microphones(include_loopback=True):
            if name.lower() in mic.name.lower():
                if not mic.isloopback:
                    raise SystemExit(
                        f"[x] 「{mic.name}」不是播放设备的环回接口，请用 --list 查看可采集项"
                    )
                return mic
        raise SystemExit(f"[x] 找不到设备：{name}（用 --list 查看）")

    speaker = sc.default_speaker()
    if speaker is None:
        raise SystemExit("[x] 系统没有默认播放设备")
    for mic in sc.all_microphones(include_loopback=True):
        if mic.isloopback and mic.name == speaker.name:
            return mic
    # 名字对不上时，退而求其次按默认扬声器名字找
    return sc.get_microphone(id=str(speaker.name), include_loopback=True)


def probe_level(device, duration: float = 0.4) -> float:
    """录一小段，返回 RMS 电平（0~1）。用来判断这个设备现在有没有声音。"""
    try:
        with device.recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_FRAMES) as rec:
            data = rec.record(numframes=int(SAMPLE_RATE * duration))
    except Exception:
        return 0.0
    if data is None or data.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(data.astype(np.float64)))))


def auto_pick_device() -> object:
    """扫一遍所有播放设备，选「现在真的有声音」的那个。"""
    candidates = list_loopback_devices()
    print("[i] 探测各播放设备（0.4 秒），看声音到底从哪个设备出来：")
    scored = []
    for mic in candidates:
        level = probe_level(mic)
        scored.append((level, mic))
        db = 20 * np.log10(level + 1e-9)
        mark = "  <== 有声音" if level > 1e-3 else ""
        print(f"      {mic.name:46s} {db:7.1f} dB{mark}")
    scored.sort(key=lambda x: x[0], reverse=True)
    if scored and scored[0][0] > 1e-3:
        return scored[0][1]
    default = resolve_device(None)
    print("[!] 探测时所有设备都是静音（还没开始放？）先用默认扬声器。")
    print("    开始播放后如果它没声音，脚本会在 6 秒后自动切到有声音的设备。")
    return default


# ---------------------------------------------------------------- 采集线程
class AudioCapture:
    """采集当前设备；如果它长时间没声音，就自动切到「真的在响」的设备。"""

    SILENT_LIMIT = 6.0   # 静音多久后开始找别的设备
    LOUD = 1e-4          # 判定「有声音」的 RMS 阈值

    def __init__(self, device, stop: threading.Event, stats: dict):
        self.device = device
        self.stop = stop
        self.stats = stats

    def _scan_others(self):
        best, best_level = None, 1e-3
        for mic in list_loopback_devices():
            if mic.name == self.device.name:
                continue
            level = probe_level(mic, 0.2)
            if level > best_level:
                best, best_level = mic, level
        return best

    def run(self, push) -> None:
        while not self.stop.is_set():
            switched = False
            with self.device.recorder(
                samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK_FRAMES
            ) as rec:
                silent_since = time.time()
                while not self.stop.is_set():
                    try:
                        data = rec.record(numframes=BLOCK_FRAMES)
                    except Exception as exc:  # 设备被拔/切换
                        print(f"\n[x] 采集中断：{exc}")
                        self.stop.set()
                        return
                    mono = data[:, 0] if data.ndim > 1 else data
                    rms = (
                        float(np.sqrt(np.mean(np.square(mono.astype(np.float64)))))
                        if mono.size
                        else 0.0
                    )
                    self.stats["blocks"] = self.stats.get("blocks", 0) + 1
                    self.stats["max_rms"] = max(self.stats.get("max_rms", 0.0), rms)
                    self.stats["cur_rms"] = rms
                    if rms > self.LOUD:
                        self.stats["last_loud"] = time.time()
                        silent_since = time.time()
                    push((np.clip(mono, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes())

                    if time.time() - silent_since > self.SILENT_LIMIT:
                        alt = self._scan_others()
                        if alt is not None:
                            self.device = alt
                            switched = True
                            break
                        silent_since = time.time()  # 都没声音，过一会儿再看
            if switched:
                print(f"[i] 上个设备没声音，已自动切换到：{self.device.name}")


# ---------------------------------------------------------------- 主流程
def wait_for_server(port: int, timeout: int = 120) -> bool:
    """等 start.bat 把模型加载完，否则一上来连 ws 会被拒绝。"""
    import urllib.request

    url = f"http://127.0.0.1:{port}/health"
    # 显式禁用代理，避免系统代理把本机请求也拦掉
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    print("[i] 等待转写服务就绪（模型加载中需要 10~30 秒）...")
    deadline = time.time() + timeout
    last_note = 0.0
    while time.time() < deadline:
        try:
            with opener.open(url, timeout=2) as resp:
                body = resp.read().decode("utf-8", "ignore")
                if resp.status == 200 and '"ready":true' in body.replace(" ", ""):
                    print("[i] 服务已就绪")
                    return True
        except Exception:
            pass
        if time.time() - last_note > 5:
            last_note = time.time()
            print("    ...还在等（服务没开就先双击 start.bat）")
        time.sleep(1)
    return False


async def run(args: argparse.Namespace) -> int:
    if not wait_for_server(args.port):
        print("\n[x] 等了 120 秒服务还没就绪。")
        print("    请先双击 start.bat，等黑窗口打印出 http://127.0.0.1:8000 再跑这个。")
        return 1

    speaker = sc.default_speaker()
    print(f"[i] 系统默认播放设备：{speaker.name if speaker else '（无）'}")

    if args.device and args.device.lower() != "auto":
        device = resolve_device(args.device)
        print(f"[i] 采集设备：{device.name}（手动指定）")
    else:
        device = auto_pick_device()
        print(f"[i] 采集设备：{device.name}")

    print(f"[i] 服务地址：ws://127.0.0.1:{args.port}")
    print("[i] 现在开始播放任何声音，字幕会出现在下面；Ctrl+C 停止\n")

    loop = asyncio.get_running_loop()
    audio_q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    stop = threading.Event()
    stats: dict = {"blocks": 0, "max_rms": 0.0, "last_loud": time.time()}

    def push(pcm: bytes) -> None:
        try:
            loop.call_soon_threadsafe(audio_q.put_nowait, pcm)
        except RuntimeError:
            pass

    worker = threading.Thread(
        target=AudioCapture(device, stop, stats).run, args=(push,), daemon=True
    )
    worker.start()

    query = [
        "encoding=linear16",
        f"sample_rate={SAMPLE_RATE}",
        "channels=1",
        "interim_results=true",
    ]
    if args.language and args.language != "auto":
        query.append(f"language={args.language}")
    url = f"ws://127.0.0.1:{args.port}/v1/listen?" + "&".join(query)

    final_lines: list[str] = []
    started = time.time()
    ui = {"interim": ""}

    def draw_status() -> None:
        """底部状态行：电平条 + 正在识别的文字。"""
        if not sys.stdout.isatty():
            return
        rms = stats.get("cur_rms", 0.0)
        db = 20 * np.log10(rms + 1e-9)
        filled = max(0, min(16, int((db + 60) / 3.75)))
        bar = "█" * filled + "░" * (16 - filled)
        text = ui["interim"][-70:]
        sys.stdout.write(
            f"\r[电平 {bar} {db:6.1f}dB] " + text + " " * max(0, 70 - len(text))
        )
        sys.stdout.flush()

    async def ui_loop() -> None:
        while not stop.is_set():
            draw_status()
            await asyncio.sleep(0.15)

    def say(text: str) -> None:
        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * 120 + "\r")
        print(text, flush=True)

    try:
        import websockets
    except ImportError:
        print("[x] 缺少 websockets，请先运行 start.bat 让环境就绪")
        return 1

    try:
        async with websockets.connect(url, max_size=None, ping_interval=20, proxy=None) as ws:
            warned = {"silent": False}

            async def sender() -> None:
                while not stop.is_set():
                    pcm = await audio_q.get()
                    await ws.send(pcm)
                    if not warned["silent"] and time.time() - stats["last_loud"] > 10:
                        warned["silent"] = True
                        say(
                            "[!] 已经 10 秒没采到声音 —— 确认电脑在播放，"
                            "且声音输出的就是上面那个设备（电平条应该动起来）"
                        )

            async def receiver() -> None:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if msg.get("type") != "Results":
                        continue
                    alts = msg.get("channel", {}).get("alternatives") or [{}]
                    text = (alts[0].get("transcript") or "").strip()
                    if msg.get("is_final"):
                        if text:
                            say(text)
                            final_lines.append(text)
                        ui["interim"] = ""
                    else:
                        ui["interim"] = text

            send_task = asyncio.create_task(sender())
            recv_task = asyncio.create_task(receiver())
            ui_task = asyncio.create_task(ui_loop())
            timeout = None if args.duration <= 0 else args.duration
            done, pending = await asyncio.wait(
                {send_task, recv_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            ui_task.cancel()
            for t in done:
                exc = t.exception()
                if exc:
                    print(f"\n[x] 连接中断：{exc}")
                    print("    服务还在跑吗？先双击 start.bat。")
                    return 1
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"\n[x] 连不上服务：{type(exc).__name__}: {exc}")
        print("    先双击 start.bat 把服务开起来，再跑这个。")
        return 1
    finally:
        stop.set()
        if sys.stdout.isatty():
            sys.stdout.write("\r" + " " * 120 + "\r")

    elapsed = time.time() - started
    peak_db = 20 * np.log10(stats.get("max_rms", 0.0) + 1e-9)
    print(f"\n[i] 结束，采集 {elapsed:.0f} 秒，共 {len(final_lines)} 段")
    print(f"[i] 采集音频峰值 {peak_db:.1f} dB（低于 -60 dB 基本就是没抓到声音）")

    text = "\n".join(final_lines)
    if text:
        out = Path(args.save) if args.save else (
            ROOT / "logs" / f"system_audio_{datetime.now():%Y%m%d_%H%M%S}.txt"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"[i] 字幕已保存：{out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="采集系统播放声音并实时转写")
    parser.add_argument("--port", type=int, default=8000, help="服务端口（默认 8000）")
    parser.add_argument("--language", default="zh", help="语言：zh / auto / en ...（默认 zh）")
    parser.add_argument(
        "--device", default="auto",
        help="播放设备名；默认 auto = 自动探测/自动跟随有声设备",
    )
    parser.add_argument("--save", default=None, help="字幕保存路径（默认 logs 下带时间戳）")
    parser.add_argument("--duration", type=int, default=0, help="采集多少秒后自动停止（0=一直录）")
    parser.add_argument("--list", action="store_true", help="列出可采集的播放设备")
    args = parser.parse_args()

    if args.list:
        print("可采集的播放设备（环回）：")
        for m in list_loopback_devices():
            print(f"  - {m.name}")
        return 0

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
