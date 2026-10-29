"""下载 faster-whisper (CTranslate2) 模型到本地 models 目录。

用法:
    python tools\download_model.py <repo_id> <目标目录名>

示例:
    python tools\download_model.py Systran/faster-whisper-small faster-whisper-small
    python tools\download_model.py deepdml/faster-whisper-large-v3-turbo-ct2 faster-whisper-large-v3-turbo

默认走 hf-mirror.com 镜像（国内可直连），可用环境变量 HF_ENDPOINT 覆盖。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

from huggingface_hub import snapshot_download  # noqa: E402

ALLOW = [
    "config.json",
    "model.bin",
    "tokenizer.json",
    "vocabulary.*",
    "preprocessor_config.json",
    "README.md",
]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    repo_id = sys.argv[1]
    target = sys.argv[2] if len(sys.argv) > 2 else repo_id.split("/")[-1]
    root = Path(__file__).resolve().parent.parent
    dest = root / "models" / target
    dest.mkdir(parents=True, exist_ok=True)
    print(f"[*] repo   : {repo_id}")
    print(f"[*] mirror : {os.environ['HF_ENDPOINT']}")
    print(f"[*] dest   : {dest}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest),
        allow_patterns=ALLOW,
        max_workers=4,
    )
    total = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"[+] 完成，共 {total / 1e6:.1f} MB")
    for f in sorted(dest.iterdir()):
        if f.is_file():
            print(f"    {f.name:28s} {f.stat().st_size / 1e6:8.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
