"""截图 OCR：用 Windows 自带的 OCR 引擎（无需下载模型）。

Windows OCR 会把每个汉字当成独立 word 返回，直接拼接会出现「你 好 世 界」，
所以这里按字框间距重建空格：中文之间不加空格，英文/数字按间距判断。
"""
from __future__ import annotations

import asyncio
import re

from PIL import Image

CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")


def _is_cjk(ch: str) -> bool:
    return bool(ch and CJK.match(ch))


def _rebuild_line(line) -> str:
    out: list[str] = []
    prev_right: float | None = None
    for word in getattr(line, "words", []) or []:
        rect = word.bounding_rect
        text = (word.text or "").strip()
        if not text:
            continue
        if prev_right is not None and out:
            gap = rect.x - prev_right
            height = rect.height or 20
            last_ch = out[-1][-1] if out[-1] else ""
            first_ch = text[:1]
            need_space = gap > height * 0.32 and not (
                _is_cjk(last_ch) and _is_cjk(first_ch)
            )
            if need_space:
                out.append(" ")
        out.append(text)
        prev_right = rect.x + rect.width
    return "".join(out).strip()


def available() -> bool:
    try:
        import winocr  # noqa: F401

        return True
    except Exception:
        return False


def _prepare(image):
    """Windows OCR 对小于 ~24px 的字很吃力，先放大再识别。"""
    img = image.convert("RGB")
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    if w < 900:
        k = 3.0
    elif w < 1800:
        k = 2.0
    elif w < 2600:
        k = 1.5
    else:
        k = 1.0
    if k > 1.0:
        img = img.resize((int(w * k), int(h * k)), Image.LANCZOS)
    return img


def recognize(image, lang: str = "zh-Hans") -> tuple[str, str]:
    """返回 (文本, 错误信息)。"""
    try:
        import winocr
    except Exception as exc:
        return "", f"缺少 OCR 组件（winocr）：{exc}"

    image = _prepare(image)

    async def _run(code: str):
        return await winocr.recognize_pil(image, code)

    result = None
    for code in (lang, "zh-Hans", "en"):
        try:
            result = asyncio.run(_run(code))
            break
        except Exception:
            continue
    if result is None:
        return "", "OCR 失败：系统可能没装中文识别语言包"

    lines = [_rebuild_line(ln) for ln in getattr(result, "lines", []) or []]
    text = "\n".join(ln for ln in lines if ln.strip())
    return text, ""
