"""WhisperLiveKit 便携版自检。

用途：排查「显卡没吃上」「模型加载失败」「依赖缺失」这类问题。
双击 check.bat 等价于运行本脚本。
"""
from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"


def _line(title: str) -> None:
    print(f"\n--- {title} " + "-" * max(0, 56 - len(title)))


def check_python() -> None:
    _line("Python 运行环境")
    print(f"  解释器   : {sys.executable}")
    print(f"  版本     : {sys.version.split()[0]}  ({platform.machine()})")
    portable = str(Path(sys.executable)).startswith(str(ROOT))
    print(f"  便携运行 : {'是（用的是包内 runtime）' if portable else '否（用的是系统 Python！）'}")


def check_cuda_libs() -> None:
    _line("内置 CUDA 运行库")
    nv = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
    if not nv.exists():
        print("  [x] 未找到 nvidia 目录，GPU 加速不可用")
        return
    for sub in sorted(p for p in nv.iterdir() if p.is_dir()):
        bin_dir = sub / "bin"
        if bin_dir.is_dir():
            dlls = list(bin_dir.glob("*.dll"))
            print(f"  {sub.name:22s} {len(dlls):3d} 个 DLL")


def check_ct2() -> None:
    _line("CTranslate2（faster-whisper 推理引擎）")
    try:
        import ctranslate2
    except Exception as exc:
        print(f"  [x] 导入失败: {exc}")
        return
    print(f"  版本     : {ctranslate2.__version__}")
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception as exc:
        print(f"  [x] 检测 CUDA 失败: {exc}")
        n = 0
    print(f"  CUDA 设备: {n}")
    if n:
        try:
            types = sorted(ctranslate2.get_supported_compute_types("cuda"))
            print(f"  可用精度 : {', '.join(types)}")
        except Exception as exc:
            print(f"  [!] 读取精度列表失败: {exc}")


def check_torch() -> None:
    _line("PyTorch")
    try:
        import torch
    except Exception as exc:
        print(f"  [x] 导入失败: {exc}")
        return
    print(f"  版本     : {torch.__version__}")
    print(f"  CUDA 可用: {torch.cuda.is_available()}  (本包用 CPU 版 torch，语音识别走 CTranslate2 GPU，不影响速度)")


def check_models() -> None:
    _line("本地模型")
    if not MODELS.exists():
        print("  [x] models 目录不存在")
        return
    found = False
    for d in sorted(p for p in MODELS.iterdir() if p.is_dir()):
        bin_file = d / "model.bin"
        if bin_file.exists():
            size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) / 1e6
            print(f"  {d.name:34s} {size:8.1f} MB")
            found = True
    if not found:
        print("  [!] 没有可用模型，首次启动会联网下载")


def check_inference() -> None:
    _line("实机推理测试")
    dirs = [d for d in sorted(MODELS.glob("*")) if (d / "model.bin").exists()]
    if not dirs:
        print("  [!] 没有本地模型，跳过")
        return
    model_dir = dirs[-1]
    print(f"  模型     : {model_dir.name}")
    try:
        import numpy as np
        from faster_whisper import WhisperModel
    except Exception as exc:
        print(f"  [x] 依赖导入失败: {exc}")
        return

    audio = (0.05 * np.sin(2 * np.pi * 220 * np.linspace(0, 3, 48000, endpoint=False))).astype("float32")

    for device, ctype in (("cuda", "float16"), ("cpu", "int8")):
        try:
            t0 = time.time()
            model = WhisperModel(str(model_dir), device=device, compute_type=ctype)
            load = time.time() - t0
            t0 = time.time()
            segments, _ = model.transcribe(audio, language="zh", beam_size=1)
            list(segments)
            infer = time.time() - t0
            print(f"  [OK] {device:4s}/{ctype:8s} 加载 {load:.2f}s  推理 {infer:.2f}s")
            if device == "cuda":
                print("  => GPU 加速可用")
            del model
        except Exception as exc:
            print(f"  [x]  {device:4s}/{ctype:8s} 失败: {type(exc).__name__}: {exc}")


def main() -> int:
    print("=" * 62)
    print("  WhisperLiveKit 便携版 自检")
    print("=" * 62)
    check_python()
    check_cuda_libs()
    check_ct2()
    check_torch()
    check_models()
    check_inference()
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
