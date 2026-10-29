"""一键搭建运行环境（install.bat 会调用它）。

做的事：
  1. 修好便携 Python 的 ._pth（打开 site-packages）
  2. 装 pip
  3. 按 requirements.txt 装依赖（走国内镜像）
  4. 把 ffmpeg 复制到 bin/
  5. 下载 faster-whisper 模型（走 hf-mirror）
  6. 生成启动预热音频

只依赖标准库，因为这个脚本要跑在刚解压出来的空 Python 上。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYDIR = ROOT / "runtime" / "python"
PYEXE = PYDIR / "python.exe"
PYPI_MIRROR = "https://repo.huaweicloud.com/repository/pypi/simple"
HF_MIRROR = "https://hf-mirror.com"
GET_PIP_SOURCES = [
    # 必须用官方最新版：老版本内嵌的 pip 依赖 distutils，Python 3.12 已经移除
    ("https://bootstrap.pypa.io/get-pip.py", 2_000_000),
]

MODELS = [
    ("deepdml/faster-whisper-large-v3-turbo-ct2", "faster-whisper-large-v3-turbo"),
    ("Systran/faster-whisper-small", "faster-whisper-small"),
]
MODEL_FILES = [
    "config.json", "model.bin", "tokenizer.json", "vocabulary.*",
    "preprocessor_config.json", "README.md",
]

TOTAL = 6
_step = 0


def step(msg: str) -> None:
    global _step
    _step += 1
    print(f"\n[{_step}/{TOTAL}] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\n[x] {msg}", flush=True)
    print("    可以重跑 install.bat，已完成的步骤会自动跳过。", flush=True)
    sys.exit(1)


def download(url: str, dest: Path, timeout: int = 60) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh, length=1 << 20)


# ---------------------------------------------------------------- 1. ._pth
def fix_pth() -> None:
    step("修好便携 Python 的模块搜索路径")
    pths = list(PYDIR.glob("python*._pth"))
    if not pths:
        fail(f"没找到 ._pth 文件，确认 {PYDIR} 是完整解压的 Python")
    for pth in pths:
        text = pth.read_text(encoding="utf-8", errors="ignore")
        lines = [ln for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if not any("site-packages" in ln for ln in lines):
            lines.append("Lib\\site-packages")
        if "import site" not in lines:
            lines.append("import site")
        pth.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
        print(f"    {pth.name} -> {lines}")


# ---------------------------------------------------------------- 2. pip
def ensure_pip() -> None:
    step("准备 pip")
    probe = subprocess.run([str(PYEXE), "-m", "pip", "--version"],
                           capture_output=True, text=True)
    if probe.returncode == 0:
        print(f"    已有 {probe.stdout.strip()}")
        return

    get_pip = PYDIR / "get-pip.py"
    ok = False
    for url, min_size in GET_PIP_SOURCES:
        print(f"    下载 {url}（约 2 MB，可能要 1~2 分钟）...")
        try:
            download(url, get_pip, timeout=300)
            if get_pip.stat().st_size >= min_size:
                ok = True
                break
            print(f"    文件不完整（{get_pip.stat().st_size} B）")
        except Exception as exc:
            print(f"    失败：{exc}")
    if not ok:
        fail("get-pip.py 下载失败，检查网络后重跑 install.bat")

    r = subprocess.run(
        [str(PYEXE), str(get_pip), "--no-warn-script-location",
         "-i", PYPI_MIRROR, "--trusted-host", "repo.huaweicloud.com"],
        cwd=str(PYDIR),
    )
    if r.returncode != 0:
        fail("安装 pip 失败")
    get_pip.unlink(missing_ok=True)


# ---------------------------------------------------------------- 3. 依赖
def install_requirements() -> None:
    step("安装依赖（约 1.5~2 GB，第一次会慢一些）")
    req = ROOT / "requirements.txt"
    r = subprocess.run([
        str(PYEXE), "-m", "pip", "install", "--no-warn-script-location",
        "--progress-bar", "off",
        "-i", PYPI_MIRROR, "--trusted-host", "repo.huaweicloud.com",
        "-r", str(req),
    ])
    if r.returncode != 0:
        fail("依赖安装失败，看上面的报错")


# ---------------------------------------------------------------- 4. ffmpeg
def setup_ffmpeg() -> None:
    step("准备 ffmpeg")
    bindir = ROOT / "bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / "ffmpeg.exe"
    if target.exists():
        print("    已存在，跳过")
        return
    code = (
        "import imageio_ffmpeg, shutil, pathlib;"
        "src = imageio_ffmpeg.get_ffmpeg_exe();"
        f"shutil.copy2(src, r'{target}');"
        "print('    ffmpeg ->', src)"
    )
    r = subprocess.run([str(PYEXE), "-c", code])
    if r.returncode != 0 or not target.exists():
        print("    [!] ffmpeg 复制失败，网页版和 REST 转写会受影响（悬浮窗内录不受影响）")


# ---------------------------------------------------------------- 5. 模型
def download_models() -> None:
    step("下载模型（large-v3-turbo 约 1.6 GB + small 0.5 GB）")
    code = f'''
import os, sys
os.environ["HF_ENDPOINT"] = "{HF_MIRROR}"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
from huggingface_hub import snapshot_download
models = {MODELS!r}
files = {MODEL_FILES!r}
root = r"{ROOT}"
for repo, name in models:
    dest = os.path.join(root, "models", name)
    if os.path.exists(os.path.join(dest, "model.bin")):
        print(f"    {{name}} 已存在，跳过")
        continue
    os.makedirs(dest, exist_ok=True)
    print(f"    正在下载 {{name}} ...")
    snapshot_download(repo_id=repo, local_dir=dest, allow_patterns=files, max_workers=4)
    print(f"    {{name}} 完成")
'''
    r = subprocess.run([str(PYEXE), "-c", code])
    if r.returncode != 0:
        print("    [!] 模型下载失败，可以稍后手动跑：")
        print("        runtime\\python\\python.exe tools\\download_model.py deepdml/faster-whisper-large-v3-turbo-ct2 faster-whisper-large-v3-turbo")


# ---------------------------------------------------------------- 6. 预热音频
def make_warmup() -> None:
    step("生成启动预热音频")
    target = ROOT / "assets" / "warmup.wav"
    if target.exists():
        print("    已存在，跳过")
        return
    target.parent.mkdir(exist_ok=True)
    code = f'''
import numpy as np, soundfile as sf
sr = 16000
t = np.linspace(0, 11.0, int(sr * 11.0), endpoint=False)
f0 = 120 + 15 * np.sin(2 * np.pi * 0.7 * t)
phase = 2 * np.pi * np.cumsum(f0) / sr
sig = sum((0.3 / k) * np.sin(k * phase) for k in range(1, 8))
sig = sig * 0.5 * (1 + np.sin(2 * np.pi * 1.1 * t))
sig = (0.06 * sig / np.max(np.abs(sig))).astype("float32")
sf.write(r"{target}", sig, sr)
print("    warmup.wav 已生成")
'''
    r = subprocess.run([str(PYEXE), "-c", code])
    if r.returncode != 0:
        print("    [!] 生成失败，不影响使用（启动时少一步预热）")


def main() -> int:
    if not PYEXE.exists():
        fail(f"没找到 {PYEXE}，请先让 install.bat 下载并解压 Python")

    print("=" * 60)
    print("  WhisperLiveKit Portable · 环境安装")
    print(f"  目录：{ROOT}")
    print("=" * 60)

    fix_pth()
    ensure_pip()
    install_requirements()
    setup_ffmpeg()
    download_models()
    make_warmup()

    print("\n" + "=" * 60)
    print("  安装完成，双击 float.bat 开始使用")
    print("  环境自检：双击 check.bat")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
