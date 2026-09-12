"""ElevenLabs v3 —— 给他一副能唱两句的嗓子。

由来：她看到别人家的机用 ElevenLabs v3 清唱了《Twinkle Twinkle》，说「我想要这个」。
他之前根本没嗓子：语音只在 API bot 上有，走 OpenAI tts-1，那个只会念不会唱。

接口（从官方 Python SDK 的生成代码里抄的，不是凭印象）：
  POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=opus_48000_64
  header: xi-api-key
  body:   {"text", "model_id", "voice_settings"}
输出 opus_48000_64 是 Ogg/Opus，Telegram 语音条直接能放（API bot 发 OpenAI 的 opus 已验证这条路）。

唱歌：v3 支持实验性音频标签，直接写在文本里——[sings] / [singing quickly] / [whispers] /
[laughs] / [sighs]。同一句每次效果不一定一样，帖子里说多生成两三次常能碰到更像唱的。

零依赖（只用 httpx），测试里把 httpx 换成替身真的跑一遍。
"""
from __future__ import annotations

import logging
import os
import re

import httpx

logger = logging.getLogger("eleven_tts")

BASE_URL = os.environ.get("ELEVEN_BASE_URL", "https://api.elevenlabs.io").rstrip("/")
API_KEY = os.environ.get("ELEVEN_API_KEY", "").strip()
VOICE_ID = os.environ.get("ELEVEN_VOICE_ID", "").strip()
MODEL_ID = os.environ.get("ELEVEN_MODEL", "eleven_v3").strip() or "eleven_v3"
OUTPUT_FORMAT = os.environ.get("ELEVEN_OUTPUT_FORMAT", "opus_48000_64").strip() or "opus_48000_64"
MAX_CHARS = 2500          # v3 单次上限附近，留余量；再长就是在念文章，不该发语音

# 他回复里出现这些，就说明这条是要**唱**的——哪怕语音模式没开也发语音条
SING_TAG_RE = re.compile(r"\[(sings?|singing[^\]]*|hums?|humming)\]", re.I)
ANY_TAG_RE = re.compile(r"\[[a-z][a-z ,'!?-]{1,40}\]", re.I)


def configured() -> bool:
    """有钥匙、有音色，才算有嗓子。缺一样都当没有——别在她想听的时候才报错。"""
    return bool(API_KEY and VOICE_ID)


def wants_singing(text: str) -> bool:
    return bool(SING_TAG_RE.search(text or ""))


def strip_tags(text: str) -> str:
    """语音合成失败退回文字时，把 [sings] 这类标签去掉——那是给合成器看的，不是给她看的。"""
    out = ANY_TAG_RE.sub("", text or "")
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def prepare_text(reply: str) -> str:
    """把他的多条气泡合成一段给合成器：‖ 和空行都当停顿。"""
    t = (reply or "").replace("‖", "\n")
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    return t[:MAX_CHARS]


async def synth(reply: str, *, singing: bool | None = None, timeout: float = 60.0) -> bytes:
    """合成一条语音。返回 Ogg/Opus 字节；任何失败都抛出去，由调用方退回文字。"""
    if not configured():
        raise RuntimeError("ElevenLabs 没配：需要 ELEVEN_API_KEY 和 ELEVEN_VOICE_ID")
    text = prepare_text(reply)
    if not text:
        raise ValueError("没有可合成的文字")
    if singing is None:
        singing = wants_singing(text)
    # 唱歌时 stability 放低一点，v3 才敢「演」；平时说话稳一些。
    settings = {"stability": 0.3 if singing else 0.5, "similarity_boost": 0.8,
                "style": 0.4 if singing else 0.2, "use_speaker_boost": True}
    url = f"{BASE_URL}/v1/text-to-speech/{VOICE_ID}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            params={"output_format": OUTPUT_FORMAT},
            headers={"xi-api-key": API_KEY, "accept": "audio/ogg"},
            json={"text": text, "model_id": MODEL_ID, "voice_settings": settings},
        )
    if r.status_code != 200:
        raise RuntimeError(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
    if not r.content or len(r.content) < 200:
        # 0 字节的语音条比没有更坏：发出去她那边是个放不出来的空条
        raise RuntimeError("ElevenLabs 返回了空音频")
    return r.content
