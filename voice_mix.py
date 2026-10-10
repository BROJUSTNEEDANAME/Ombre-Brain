"""语音条拼接：台词 + 亲吻 + 水声。

由来 2026-10-10：她要 sanqianzilanyue/ai-voice-breath-kiss-water 那一套。那篇的结论：
- 嗓子只念台词和喘。亲吻、水声让嗓子念，出来都是演的（亲被念成「qiu qiu」，水声像嘴里啵啵）。
- 亲吻：让同一副嗓子真亲几遍，剪成一盒（亲盒），用的时候随机抓一口，不重样，
  快慢差一点点（只许放慢不许加快），最近用过的十口躲开。亲盒由 scripts/make-kissbox.py 在 VPS 上做。
- 水声：真实录音（CC0）垫在人声底下。按峰对齐：水声的峰＝这段人声的峰 −12 dB；
  进来 0.5 秒淡入，话说完水声再留 1 秒、用 1.4 秒淡出。亲吻那一块不垫水声。
  水声素材她在 Telegram 里用 /water 发给他，存进 voice_assets/water/。

他怎么写（人设里教了）：
- 单独一行 `[kiss]` / `[kiss deep]` → 这里插一口亲吻。
- 声口标签里多写一个 wet，比如 `[low, close, wet]` → 这几句底下垫水声；
  下一个标签里不写 wet，水声就退掉。wet 这个词送进嗓子前会摘掉，嗓子看不到就不会去演。

稳妥第一：没有 ffmpeg、亲盒是空的、没有水声素材——就退回原来那条路（整段一次合成），
标记全部去掉。宁可没有亲吻声，也不能让她收不到语音。
"""
from __future__ import annotations

import asyncio
import collections
import glob
import os
import random
import re
import shutil
import subprocess

import numpy as np

import eleven_tts

ASSET_DIR = (os.environ.get("CC_VOICE_ASSETS", "").strip()
             or os.path.join(os.path.dirname(os.path.abspath(__file__)), "voice_assets"))
KISS_DIR = os.path.join(ASSET_DIR, "kissbox")
WATER_DIR = os.path.join(ASSET_DIR, "water")
SR = 48000
AUDIO_EXT = (".wav", ".mp3", ".ogg", ".oga", ".opus", ".m4a", ".flac", ".aac")

KISS_LINE_RE = re.compile(
    r"^[ \t]*\[(?:(deep|soft|light|neck)[ \t]+)?kiss(?:es)?(?:[ \t]+(deep|soft|light|neck))?[ \t]*\][ \t]*$",
    re.I)
_TAG_RE = re.compile(r"\[([^\[\]\n]{1,60})\]")
_WET_RE = re.compile(r"\bwet\b", re.I)

WATER_GAP_DB = 12.0       # 水声峰比人声峰低多少
WATER_FADE_IN = 0.5
WATER_TAIL = 1.0          # 话说完水声再留多久
WATER_FADE_OUT = 1.4
GAP = 0.12                # 段与段之间的小停顿
# 念完原调放慢。她：「语速慢一点点，就慢一点点」。v4 不认 speed 杆（参考文：0.8 和 1.0
# 出来一样长），所以念完用 ffmpeg atempo 放慢，音高不变。参考文用 0.9；她要「一点点」→ 0.93。
TEMPO_MIN, TEMPO_MAX = 0.8, 1.2     # 她用 /speed 调，慢到 0.8、快到 1.2
try:
    TEMPO = min(TEMPO_MAX, max(TEMPO_MIN, float(os.environ.get("CC_VOICE_TEMPO", "0.93") or 0.93)))
except ValueError:
    TEMPO = 0.93
_recent_kisses: collections.deque = collections.deque(maxlen=10)


# ── 解析 ──

def _strip_wet_tag(m: re.Match) -> str:
    inner = m.group(1)
    if not _WET_RE.search(inner):
        return m.group(0)
    parts = [p.strip() for p in re.split(r",|\band\b", _WET_RE.sub("", inner)) if p.strip()]
    return f"[{', '.join(parts)}]" if parts else ""


def strip_wet(text: str) -> str:
    """把标签里的 wet 摘掉（[wet] 整个删掉）。嗓子看不到它才不会去演。"""
    return _TAG_RE.sub(_strip_wet_tag, text or "")


def _has_wet(text: str) -> bool | None:
    """这段里有标签：有 wet 就 True，没有就 False；一个标签都没有：None（沿用上一段）。"""
    tags = _TAG_RE.findall(text or "")
    if not tags:
        return None
    return any(_WET_RE.search(t) for t in tags)


def plan(reply: str) -> list[dict]:
    """把他的回复拆成一段一段：{"kind":"say","text","wet"} 或 {"kind":"kiss","type"}。"""
    out: list[dict] = []
    buf: list[str] = []
    wet = False

    def flush() -> None:
        nonlocal wet, buf
        block = "\n".join(buf).strip()
        buf = []
        if not block:
            return
        w = _has_wet(block)
        wet = wet if w is None else w
        out.append({"kind": "say", "text": strip_wet(block), "wet": wet})

    for line in (reply or "").replace("‖", "\n").split("\n"):
        m = KISS_LINE_RE.match(line)
        if m:
            flush()
            kind = (m.group(1) or m.group(2) or "light").lower()
            out.append({"kind": "kiss", "type": "deep" if kind == "deep" else "light"})
        else:
            buf.append(line)
    flush()
    return out


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
    "心疼": "gentle, concerned", "哄": "soft, soothing", "想你": "longing", "吃醋": "quiet, jealous",
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
    """把她定的声口换到第一句台词的开头（他自己写的开头标签让位；wet 留着）。"""
    if not tone:
        return reply
    lines = (reply or "").split("\n")
    for i, line in enumerate(lines):
        if not line.strip() or KISS_LINE_RE.match(line) or _TRANS_LINE_RE.match(line):
            continue
        m = _LEAD_TAG_RE.match(line)
        wet = bool(m and _WET_RE.search(m.group(1)))
        body = line[m.end():] if m else line.lstrip()
        lines[i] = f"[{tone}{', wet' if wet else ''}] {body}"
        break
    return "\n".join(lines)


def strip_markers(reply: str) -> str:
    """亲吻行和 wet 都去掉——给不走拼接的那条路、也给字幕用。"""
    # ‖ 是他分条的记号，文字那条路靠它拆成几条消息——这里只删亲吻，‖ 原样留着
    lines = []
    for line in (reply or "").split("\n"):
        pieces = [p for p in line.split("‖") if not KISS_LINE_RE.match(p)]
        if pieces:
            lines.append("‖".join(pieces))
    return strip_wet("\n".join(lines))


# ── 素材 ──

def have_ffmpeg() -> bool:
    return bool(shutil.which("ffmpeg"))


def kiss_files(kind: str | None = None) -> list[str]:
    kinds = [kind] if kind else ["light", "deep"]
    out: list[str] = []
    for k in kinds:
        out += sorted(glob.glob(os.path.join(KISS_DIR, k, "*.wav")))
    return out


def water_files() -> list[str]:
    return sorted(p for p in glob.glob(os.path.join(WATER_DIR, "*"))
                  if p.lower().endswith(AUDIO_EXT))


# ── 音频小工具（同步，丢进线程里跑）──

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


def slow_down(audio: bytes) -> bytes:
    """整条语音原调变速到 TEMPO（小于 1 放慢，大于 1 加快），音高不变。"""
    return encode_opus(decode(audio, af=f"atempo={TEMPO:.3f}"))


def peak_db(x: np.ndarray) -> float:
    p = float(np.max(np.abs(x))) if x.size else 0.0
    return 20 * np.log10(p) if p > 1e-9 else -120.0


def _fade(x: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    x = x.copy()
    a = min(len(x), int(fade_in * SR))
    b = min(len(x), int(fade_out * SR))
    if a:
        x[:a] *= np.linspace(0, 1, a, dtype=np.float32)
    if b:
        x[-b:] *= np.linspace(1, 0, b, dtype=np.float32)
    return x


def pick_water_window(water: np.ndarray, n: int, rng: random.Random) -> np.ndarray:
    """随机切两个窗，挑响动多的那个（量峰值）。素材不够长就接着循环。"""
    if water.size == 0:
        return np.zeros(n, dtype=np.float32)
    if len(water) < n:
        water = np.tile(water, n // len(water) + 1)
    best = None
    for _ in range(2):
        start = rng.randint(0, len(water) - n)
        w = water[start:start + n]
        if best is None or peak_db(w) > peak_db(best):
            best = w
    return best


def lay_water(voice: np.ndarray, water: np.ndarray, rng: random.Random) -> np.ndarray:
    """人声不动，只动水声：峰对齐到人声峰 −12 dB，淡入淡出，话说完再留 1 秒。"""
    tail = int(WATER_TAIL * SR)
    voice = np.concatenate([voice, np.zeros(tail, dtype=np.float32)])
    w = pick_water_window(water, len(voice), rng)
    gain_db = (peak_db(voice) - WATER_GAP_DB) - peak_db(w)
    w = _fade(w * np.float32(10 ** (gain_db / 20)), WATER_FADE_IN, WATER_FADE_OUT)
    return voice + w


def pick_kiss(kind: str, rng: random.Random) -> str | None:
    files = kiss_files(kind) or kiss_files()
    if not files:
        return None
    fresh = [f for f in files if f not in _recent_kisses] or files
    f = rng.choice(fresh)
    _recent_kisses.append(f)
    return f


# ── 主流程 ──

async def render(reply: str, *, singing: bool | None = None,
                 rng: random.Random | None = None) -> bytes:
    """合成一条语音条（Ogg/Opus）。任何失败都抛出去，由桥退回文字。"""
    rng = rng or random.Random()
    reply = drop_translations(reply)       # 译文只进字幕，不念
    if singing is None:
        singing = eleven_tts.wants_singing(reply)
    segs = [] if singing else plan(reply)
    can_mix = have_ffmpeg()
    if not (can_mix and kiss_files()):
        segs = [s for s in segs if s["kind"] != "kiss"]
    waters = water_files() if can_mix else []
    if not waters:
        for s in segs:
            s["wet"] = False
    if not any(s["kind"] == "kiss" or s.get("wet") for s in segs):
        # 原来那条路：整段一口气念（参考文：喘才不会断），念完放慢一点
        audio = await eleven_tts.synth(strip_markers(reply), singing=singing)
        if singing or abs(TEMPO - 1.0) < 0.001 or not can_mix:
            return audio          # 唱歌不动节拍；没有 ffmpeg 就原样发
        try:
            return await asyncio.to_thread(slow_down, audio)
        except Exception:  # noqa: BLE001
            return audio          # 放慢失败也照样有声音，绝不因为这个让她收不到

    says = [s for s in segs if s["kind"] == "say"]
    texts = [eleven_tts.prepare_text(s["text"]) for s in says]
    # 每段都把前后句递给嗓子，语气才接得上
    audio: dict[int, bytes] = {}
    for i, s in enumerate(says):
        if not texts[i]:
            continue
        audio[i] = await eleven_tts.synth(
            s["text"], previous_text=texts[i - 1] if i else "",
            next_text=texts[i + 1] if i + 1 < len(texts) else "")
    if not audio:
        raise ValueError("没有可合成的台词")
    water_src = rng.choice(waters) if waters else None
    return await asyncio.to_thread(_assemble, segs, says, audio, water_src, rng)


def _assemble(segs, says, audio, water_src, rng) -> bytes:
    gap = np.zeros(int(GAP * SR), dtype=np.float32)
    water = decode(water_src) if water_src else np.zeros(0, dtype=np.float32)
    parts: list[np.ndarray] = []
    i_say = 0
    for s in segs:
        if s["kind"] == "say":
            idx = i_say
            i_say += 1
            if idx not in audio:
                continue
            v = decode(audio[idx], af=f"atempo={TEMPO:.3f}" if abs(TEMPO - 1.0) >= 0.001 else "")
            if s.get("wet") and water.size:
                v = lay_water(v, water, rng)
            parts.append(v)
        else:
            f = pick_kiss(s["type"], rng)
            if not f:
                continue
            # 快慢随机差一点点：只许放慢（≤3%），不许加快
            parts.append(decode(f, af=f"atempo={rng.uniform(0.97, 1.0):.3f}"))
        parts.append(gap)
    return encode_opus(np.concatenate(parts))
