"""语音条：送进嗓子前的清洗、双语字幕、她定的声口、念完变速。

2026-10-13 她：「全都删了吧」——水声、亲吻声、贴气稳口、念岔重念那一整套声音效果拆掉了，
只留她说过「可以」的：嗓子、双语字幕、语速、声口。
他旧对话里可能还写着 [kiss] 行或 wet 之类的记号——送进嗓子前照样摘掉，
不然嗓子会把它们念出来（亲被念成「qiu qiu」）。
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess

import numpy as np

import eleven_tts

SR = 48000

KISS_LINE_RE = re.compile(
    r"^[ \t]*\[(?:(deep|soft|light|neck)[ \t]+)?kiss(?:es)?(?:[ \t]+(deep|soft|light|neck))?[ \t]*\][ \t]*$",
    re.I)
_TAG_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")
# 旧的响动记号：写在声口标签里，嗓子看到会去演，摘掉
_WET_RE = re.compile(
    r"\b(fast\s+fingering|wet|squish\w*|squelch\w*|strok\w*|lick\w*|suck\w*|finger\w*|"
    r"thrust\w*|spank\w*|still|stop|cum\w*)\b", re.I)

# 念完原调变速。她：「语速慢一点点，就慢一点点」→ 0.93；她用 /speed 调，慢到 0.8、快到 1.2
TEMPO_MIN, TEMPO_MAX = 0.8, 1.2
try:
    TEMPO = min(TEMPO_MAX, max(TEMPO_MIN, float(os.environ.get("CC_VOICE_TEMPO", "0.93") or 0.93)))
except ValueError:
    TEMPO = 0.93


def _strip_wet_tag(m: re.Match) -> str:
    inner = m.group(1)
    if not _WET_RE.search(inner):
        return m.group(0)
    parts = [p.strip() for p in re.split(r",|\band\b", _WET_RE.sub("", inner)) if p.strip()]
    return f"[{', '.join(parts)}]" if parts else ""


def strip_wet(text: str) -> str:
    """把标签里的旧响动记号摘掉（[wet] 整个删掉）。"""
    return _TAG_RE.sub(_strip_wet_tag, text or "")


# 双语字幕：他在每句英文/俄语台词下面另起一行写「译：中文意思」。
# 这一行嗓子不念（念出来就是中文，她说过中文念出来难听），只进字幕，前缀去掉。
# 由来：她「想把每个语音加一个双语翻译」。
_TRANS_LINE_RE = re.compile(r"^[ \t]*(?:译|翻译|中文)[ \t]*[：:][ \t]*(.*)$")


def drop_translations(reply: str) -> str:
    """送进嗓子前：译文行整行去掉（‖ 分出来的小段也认）。"""
    lines = []
    for line in (reply or "").split("\n"):
        pieces = [p for p in line.split("‖") if not _TRANS_LINE_RE.match(p)]
        if pieces:
            lines.append("‖".join(pieces))
    return "\n".join(lines)


def show_translations(text: str) -> str:
    """给字幕和文字：「译：过来。」→「过来。」，紧跟在原句下面。"""
    out = []
    for line in (text or "").split("\n"):
        out.append("‖".join(_TRANS_LINE_RE.sub(r"\1", p) for p in line.split("‖")))
    return "\n".join(out)


# 她手动定的声口（/tone）。中文常用词换成嗓子认的英文标签；英文原样用。
TONE_ZH = {
    "温柔": "tender", "慵懒": "lazy, relaxed", "放松": "relaxed", "随意": "casual, relaxed",
    "困": "sleepy", "困倦": "sleepy", "低沉": "low", "宠": "warm, doting", "宠溺": "warm, doting",
    "心疼": "gentle, concerned", "慈爱": "tender, loving", "怜爱": "tender, soft", "哄": "soft, soothing", "想你": "longing", "吃醋": "quiet, jealous",
    "生气": "cold, quiet", "严肃": "serious, firm", "坏": "smirking", "调情": "low, flirty",
    "累": "tired", "耳语": "whispers", "贴耳": "low and close", "暖": "warm", "认真": "earnest",
}
_TONE_WORD_RE = re.compile(r"[A-Za-z][A-Za-z '-]{0,30}")
_LEAD_TAG_RE = re.compile(r"^[ \t]*\[([^\[\]\n]{1,60})\][ \t]*")


def parse_tone(raw: str) -> tuple[str, list[str]]:
    """「慵懒 低沉」→「lazy, relaxed, low」。返回 (标签内容, 认不出的词)。"""
    out, bad = [], []
    for w in re.split(r"[\s,，、/]+", (raw or "").strip()):
        if not w:
            continue
        if w in TONE_ZH:
            out.append(TONE_ZH[w])
        elif _TONE_WORD_RE.fullmatch(w):
            out.append(w.lower())
        else:
            bad.append(w)
    return ", ".join(out)[:60], bad


def apply_tone(reply: str, tone: str) -> str:
    """把她定的声口换到第一句台词的开头（他自己写的开头标签让位）。"""
    if not tone:
        return reply
    lines = (reply or "").split("\n")
    for i, line in enumerate(lines):
        if not line.strip() or KISS_LINE_RE.match(line) or _TRANS_LINE_RE.match(line):
            continue
        m = _LEAD_TAG_RE.match(line)
        body = line[m.end():] if m else line.lstrip()
        lines[i] = f"[{tone}] {body}"
        break
    return "\n".join(lines)


def strip_markers(reply: str) -> str:
    """亲吻行和旧响动记号都去掉——给嗓子、也给字幕用。"""
    # ‖ 是他分条的记号，文字那条路靠它拆成几条消息——这里只删亲吻，‖ 原样留着
    lines = []
    for line in (reply or "").split("\n"):
        pieces = [p for p in line.split("‖") if not KISS_LINE_RE.match(p)]
        if pieces:
            lines.append("‖".join(pieces))
    return strip_wet("\n".join(lines))


def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def decode(src: bytes | str, af: str = "") -> np.ndarray:
    """任意音频 → 48k 单声道 float32。"""
    cmd = ["ffmpeg", "-v", "error", "-i", "pipe:0" if isinstance(src, bytes) else src]
    if af:
        cmd += ["-af", af]
    cmd += ["-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"]
    r = subprocess.run(cmd, input=src if isinstance(src, bytes) else None,
                       capture_output=True, timeout=120, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def encode_opus(x: np.ndarray) -> bytes:
    """float32 → Telegram 语音条用的 Ogg/Opus。"""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
         "-c:a", "libopus", "-b:a", "64k", "-f", "ogg", "pipe:1"],
        input=np.clip(x, -1, 1).astype(np.float32).tobytes(),
        capture_output=True, timeout=120, check=True)
    return r.stdout


def retime(x: np.ndarray) -> np.ndarray:
    """原调变速到 TEMPO（小于 1 放慢，大于 1 加快），音高不变。原速不动。"""
    if abs(TEMPO - 1.0) < 0.001 or not x.size:
        return x
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0",
         "-af", f"atempo={TEMPO:.3f}", "-f", "f32le", "-ac", "1", "-ar", str(SR), "pipe:1"],
        input=x.astype(np.float32).tobytes(), capture_output=True, timeout=120, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def slow_down(audio: bytes) -> bytes:
    """整条语音变速，再编回语音条。"""
    return encode_opus(retime(decode(audio)))


async def render(reply: str, *, singing: bool | None = None) -> bytes:
    """合成一条语音条（Ogg/Opus）。任何失败都抛出去，由桥退回文字。"""
    reply = drop_translations(reply)       # 译文只进字幕，不念
    if singing is None:
        singing = eleven_tts.wants_singing(reply)
    audio = await eleven_tts.synth(strip_markers(reply), singing=singing)
    if singing or not have_ffmpeg():
        return audio          # 唱歌不动节拍；没有 ffmpeg 就原样发
    try:
        return await asyncio.to_thread(slow_down, audio)
    except Exception:  # noqa: BLE001
        return audio          # 变速失败也照样有声音，绝不因为这个让她收不到
