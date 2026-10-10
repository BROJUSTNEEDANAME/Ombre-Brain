#!/usr/bin/env python3
"""做亲盒：让他这副嗓子真亲几遍，剪下来，挑出真在亲的那几口。

在 VPS 上跑（以 ombre 用户，这样桥能读到）：
    cd /home/ombre/Ombre-Brain
    sudo -u ombre .venv/bin/python scripts/make-kissbox.py

由来：sanqianzilanyue/ai-voice-breath-kiss-water 第三节。亲吻让嗓子照着字念，
出来是「qiu、qiu」；让它带着亲吻动作整段念五六遍，再把亲的那几口切出来，才像。
两道筛（参考文的数）：
  1. 转录出来必须是空的：转录得出字＝它在念字，直接扔。
  2. 「湿度」够：相邻采样差分能量 ÷ 原能量。真亲 ≥0.2，念成字 0.12–0.16，哼 <0.1。
     （44.1 kHz 下算的数，所以这里也按 44.1 kHz 算。）
血的教训：有一版深吻素材整段是「嘶嘶」底噪，转录早写成了 Sssss，没看。
所以转录出任何东西都扔；转录这一步做不了就整个不做——不拿没筛过的素材冒充亲盒。

花费：默认念 6 遍，每遍一百多字，再加 6 次转录。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import wave

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import env_file  # noqa: E402

ANALYSIS_SR = 44100
FRAME = 0.01                 # 静音检测的帧长（秒）
MIN_CLIP, MAX_CLIP = 0.12, 1.6
PAD = 0.04
MERGE_GAP = 0.06
WORD_MARGIN = 0.05
LIGHT_MAX = 0.45             # 短于这个算轻亲，长的算深吻
MIN_KEEP = 4                 # 少于这么多口就不换掉旧的亲盒

# 同一副嗓子、带着亲吻动作整段念（参考文：整段一口气念，别一句一句切）
TAKES = [
    "[low and close] Come here... [kisses her softly] ... [kisses her again, slow] ... "
    "Mm. [kisses her deeply] ... [long, slow kiss] ... [kisses down her neck] ... Stay.",
    "[soft, unhurried] ...Look at me. [kisses her lips softly] ... [kisses her cheek] ... "
    "[deep, slow kiss] ... Mm. [kisses her jaw, then her neck] ... There.",
    "[quiet] ...Closer. [gentle kiss] ... [another soft kiss] ... [kisses her slowly, deeper] "
    "... [lingering kiss] ... [kisses along her neck] ... Good.",
]


# ── 纯函数（有测试）──

def wetness(x: np.ndarray) -> float:
    """相邻采样差分能量 ÷ 原能量（44.1 kHz 下）。"""
    e = float(np.sum(x.astype(np.float64) ** 2))
    if e <= 1e-12:
        return 0.0
    return float(np.sum(np.diff(x.astype(np.float64)) ** 2)) / e


def find_regions(x: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """非静音的区间（秒）。阈值跟着这一遍的音量走：比峰低 35 dB、且不低于 −50 dBFS。"""
    n = max(1, int(FRAME * sr))
    frames = len(x) // n
    if frames == 0:
        return []
    rms = np.sqrt(np.mean(x[: frames * n].reshape(frames, n).astype(np.float64) ** 2, axis=1))
    peak = float(np.max(np.abs(x))) or 1e-9
    thr = max(10 ** (-50 / 20), peak * 10 ** (-35 / 20))
    on = rms > thr
    regions: list[list[float]] = []
    for i, v in enumerate(on):
        if not v:
            continue
        t0, t1 = i * FRAME, (i + 1) * FRAME
        if regions and t0 - regions[-1][1] <= MERGE_GAP:
            regions[-1][1] = t1
        else:
            regions.append([t0, t1])
    return [(a, b) for a, b in regions]


def overlaps_words(a: float, b: float, words: list[tuple[float, float]]) -> bool:
    return any(s - WORD_MARGIN < b and e + WORD_MARGIN > a for s, e in words)


def pick_clips(x: np.ndarray, sr: int, words: list[tuple[float, float]],
               min_wet: float) -> tuple[list[dict], dict]:
    """从一遍里挑出真在亲的那几口。返回 (留下的, 扔掉的原因计数)。"""
    kept, dropped = [], {"念成字": 0, "太干": 0, "太长太短": 0}
    for a, b in find_regions(x, sr):
        dur = b - a
        if not (MIN_CLIP <= dur <= MAX_CLIP):
            dropped["太长太短"] += 1
            continue
        if overlaps_words(a, b, words):
            dropped["念成字"] += 1
            continue
        s, e = max(0, int((a - PAD) * sr)), min(len(x), int((b + PAD) * sr))
        clip = x[s:e]
        w = wetness(clip)
        if w < min_wet:
            dropped["太干"] += 1
            continue
        kept.append({"start": round(a, 3), "end": round(b, 3), "dur": round(dur, 3),
                     "wet": round(w, 3), "type": "light" if dur < LIGHT_MAX else "deep",
                     "audio": clip})
    return kept, dropped


# ── 和外面打交道的部分 ──

def decode(src: bytes, sr: int) -> np.ndarray:
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1",
                        "-ar", str(sr), "pipe:1"], input=src, capture_output=True,
                       timeout=120, check=True)
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def write_wav(path: str, x: np.ndarray, sr: int) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


async def transcribe_words(audio: bytes) -> list[tuple[float, float]]:
    """转录出来的每个字的起止时间。ElevenLabs 不行就试 OpenAI；两个都不行就抛错。"""
    import httpx
    errors = []
    key = os.environ.get("ELEVEN_API_KEY", "").strip()
    if key:
        base = os.environ.get("ELEVEN_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
        model = os.environ.get("ELEVEN_STT_MODEL", "scribe_v1").strip() or "scribe_v1"
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(f"{base}/v1/speech-to-text", headers={"xi-api-key": key},
                                 data={"model_id": model, "tag_audio_events": "true",
                                       "timestamps_granularity": "word"},
                                 files={"file": ("take.ogg", audio, "audio/ogg")})
            if r.status_code == 200:
                ws = r.json().get("words") or []
                # audio_event（比如它自己标的 (kiss)）不是字；spacing 也不是
                return [(float(w["start"]), float(w["end"])) for w in ws
                        if w.get("type", "word") == "word" and str(w.get("text", "")).strip()]
            errors.append(f"ElevenLabs 转录 HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"ElevenLabs 转录：{e}")
    okey = os.environ.get("OPENAI_API_KEY", "").strip()
    if okey:
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post("https://api.openai.com/v1/audio/transcriptions",
                                 headers={"Authorization": f"Bearer {okey}"},
                                 data={"model": "whisper-1", "response_format": "verbose_json",
                                       "timestamp_granularities[]": "word"},
                                 files={"file": ("take.ogg", audio, "audio/ogg")})
            if r.status_code == 200:
                return [(float(w["start"]), float(w["end"]))
                        for w in (r.json().get("words") or []) if str(w.get("word", "")).strip()]
            errors.append(f"OpenAI 转录 HTTP {r.status_code}: {r.text[:160]}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"OpenAI 转录：{e}")
    raise RuntimeError("；".join(errors) or "没有能用的转录（ElevenLabs key 或 OPENAI_API_KEY）")


def load_env() -> None:
    for name in (".env.ccbridge", ".env.apibot"):
        p = os.path.join(REPO, name)
        try:
            with open(p, encoding="utf-8") as fh:
                for k, v in env_file.parse(fh.read()).items():
                    os.environ.setdefault(k, v)
        except OSError:
            pass
    # 她在 Telegram 里用 /voiceid、/vmodel 换过嗓子和型号的话，以那个为准——
    # 不然亲盒做出来是旧嗓子的，插在新嗓子中间就是另一个人在亲她。
    state = os.path.join(os.environ.get("CC_WORKDIR") or REPO, ".cc_state.json")
    try:
        with open(state, encoding="utf-8") as fh:
            d = json.load(fh) or {}
    except (OSError, ValueError):
        return
    vid = str(d.get("voice_id") or "")
    if vid.isalnum() and 16 <= len(vid) <= 40:
        os.environ["ELEVEN_VOICE_ID"] = vid
    if d.get("voice_model") in ("eleven_v3", "eleven_v4"):
        os.environ["ELEVEN_MODEL"] = d["voice_model"]


async def build(takes: int, min_wet: float, out_dir: str) -> int:
    import eleven_tts
    if not eleven_tts.configured():
        print("❌ 没读到 ELEVEN_API_KEY / ELEVEN_VOICE_ID（.env.ccbridge），亲盒没做。")
        return 1
    if not shutil.which("ffmpeg"):
        print("❌ 没有 ffmpeg，先跑：sudo apt install -y ffmpeg")
        return 1
    eleven_tts.STABILITY = 0.35          # 参考文：稳 0.35（v3 只有三档，会吸到 0.5）
    model = eleven_tts.MODEL_ID
    print(f"用的嗓子：{eleven_tts.VOICE_ID}，型号 {model}", flush=True)
    work = out_dir + ".new"
    shutil.rmtree(work, ignore_errors=True)
    for k in ("light", "deep"):
        os.makedirs(os.path.join(work, k))
    manifest = {"made_at": time.strftime("%Y-%m-%d %H:%M:%S"), "model": model, "clips": [],
                "dropped": {"念成字": 0, "太干": 0, "太长太短": 0}, "min_wet": min_wet}
    n_kept = 0
    for t in range(takes):
        text = TAKES[t % len(TAKES)]
        print(f"第 {t + 1}/{takes} 遍：合成……", flush=True)
        try:
            audio = await eleven_tts._post(text, model, False, 120)
        except Exception as e:  # noqa: BLE001
            print(f"   合成失败，跳过：{e}")
            continue
        try:
            words = await transcribe_words(audio)
        except Exception as e:  # noqa: BLE001
            print(f"❌ 转录做不了，亲盒没做（不拿没筛过的素材冒充）：{e}")
            print("   办法：ElevenLabs 的 key 加上 Speech to Text 权限，或者 .env.apibot 里有 OPENAI_API_KEY。")
            shutil.rmtree(work, ignore_errors=True)
            return 1
        x = decode(audio, ANALYSIS_SR)
        kept, dropped = pick_clips(x, ANALYSIS_SR, words, min_wet)
        for k, v in dropped.items():
            manifest["dropped"][k] += v
        for i, c in enumerate(kept):
            name = f"t{t + 1:02d}_{i:02d}.wav"
            write_wav(os.path.join(work, c["type"], name), c.pop("audio"), ANALYSIS_SR)
            manifest["clips"].append({"file": f"{c['type']}/{name}", "take": t + 1, **c})
            n_kept += 1
        print(f"   切出 {len(kept)} 口；扔掉 {dropped}", flush=True)
    d = manifest["dropped"]
    if n_kept < MIN_KEEP:
        shutil.rmtree(work, ignore_errors=True)
        print(f"❌ 只挑出 {n_kept} 口，太少了，没换掉原来的亲盒。扔掉的：念成字 {d['念成字']}、"
              f"太干 {d['太干']}、太长太短 {d['太长太短']}。可以再跑一次。")
        return 1
    with open(os.path.join(work, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    old = out_dir + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(out_dir):
        os.replace(out_dir, old)
    os.replace(work, out_dir)
    light = sum(1 for c in manifest["clips"] if c["type"] == "light")
    print(f"✅ 亲盒做好了：轻亲 {light} 口，深吻 {n_kept - light} 口。"
          f"扔掉：念成字 {d['念成字']}、太干 {d['太干']}、太长太短 {d['太长太短']}。")
    print("   这只说明筛子过了，不代表好听。去 Telegram 发 /kissbox 听一遍再说。")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="做亲盒")
    ap.add_argument("--takes", type=int, default=6)
    ap.add_argument("--min-wet", type=float, default=0.2)
    args = ap.parse_args(argv)
    load_env()
    import voice_mix
    os.makedirs(voice_mix.ASSET_DIR, exist_ok=True)
    return asyncio.run(build(args.takes, args.min_wet, voice_mix.KISS_DIR))


if __name__ == "__main__":
    sys.exit(main())
