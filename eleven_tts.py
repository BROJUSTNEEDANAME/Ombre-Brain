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
# 默认 v4：她第一次听 v3 就说「没感情，像非常机械的念台词」。ElevenLabs 官方说 v4
# 是情感表现最丰富的一代（model id eleven_v4）。v4 合成失败会用同一副嗓子退回 v3 再试一次——
# 同一副嗓子换型号，不是换一副嗓子（参考文：中途换嗓比安静一秒难受得多）。
MODEL_ID = os.environ.get("ELEVEN_MODEL", "eleven_v4").strip() or "eleven_v4"
FALLBACK_MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = os.environ.get("ELEVEN_OUTPUT_FORMAT", "opus_48000_64").strip() or "opus_48000_64"
# stability 越低情绪起伏越大、越高越平。0.5 她听成「机械」；降到 0.3 又「太有情绪、
# 太活泼，不像他平常会说的」，她说压一点点就行 → 0.4。参考文：0.35 有戏，0.5 像平常说话。
STABILITY = float(os.environ.get("ELEVEN_STABILITY", "0.4") or 0.4)
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
    out = re.sub(r"[ \t]{2,}", " ", out)
    # 去掉标签后行首会剩一个空格（「[sings] 歌词」→「 歌词」），字幕里看着歪
    return "\n".join(x.strip(" \t") for x in out.split("\n")).strip()


# ── 送进嗓子之前的清洗 ──
# 由来：她捏好嗓子一测「不太好」。查下来送进合成器的是**原始回复**——
# （低笑）（把你按进怀里）这类动作括号、*（蚁巢内心）*、小尼那一行、颜文字、
# 「过来 坐好」这种无标点空格，全都原样交给嗓子去念。嗓子把「低笑」两个字念出来，
# 把空格当没有，一口气平推到底。参考 ai-voice-breath-kiss-water：嗓子只念台词和喘。
_INNER_RE = re.compile(r"\*[（(][^*]*?[)）]\*|\*[^*\n]{1,200}\*")     # *（内心）* / *动作*
_PAREN_RE = re.compile(r"[（(][^（()）]{0,200}[)）]")                     # 一层括号
_STAMP_RE = re.compile(r"^[ \t]*[\[【]?\d{1,4}[-/.:：]\d{1,2}(?:[-/.:：]\d{1,2})?"
                       r"(?:[ T]\d{1,2}[:：]\d{2})?[\]】]?[ \t|｜]*", re.M)
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]")
_MD_RE = re.compile(r"[*_`#>~]+")
_CJK = "\u4e00-\u9fff"
_CJK_SPACE_RE = re.compile(rf"(?<=[{_CJK}])[ \u3000]+(?=[{_CJK}])")
# 猛词一上嗓子就开始「演」（参考文：intense/heavy/growl 全删了）。整个标签丢掉。
_LOUD_TAG_RE = re.compile(
    r"\[[^\]]*\b(intense|heav(y|ily)|growl\w*|strain\w*|rough\w*|scream\w*|shout\w*|"
    r"moan\w*|pant\w*|gasp\w*|yell\w*)\b[^\]]*\]", re.I)
# 括号里写的动作，少数几种本来就是「声音」——换成轻标签留下，其余一律不念。
_SOUND_ACTIONS = (
    (re.compile(r"笑"), "[quiet laugh]"),
    (re.compile(r"叹"), "[sighs]"),
    (re.compile(r"耳边|耳朵|贴.{0,3}耳|咬耳|低声|小声|压低|凑近"), "[low and close]"),
)
MAX_TAGS = 3   # 参考文：一轮两三个就够，多了就是在演


def _paren_to_tag(m: re.Match) -> str:
    inner = m.group(0)[1:-1]
    for rx, tag in _SOUND_ACTIONS:
        if rx.search(inner):
            return f" {tag} "
    return " "


def speakable(reply: str) -> str:
    """只留嗓子该念的：台词、停顿、少量轻标签。动作/内心/颜文字/时间戳都不念。"""
    t = (reply or "").replace("‖", "\n")
    t = re.sub(r"\*\*(.+?)\*\*", r"\1", t)     # **加粗** 是强调，字要念；单星号 *…* 才是动作
    t = _INNER_RE.sub(" ", t)
    t = _STAMP_RE.sub("", t)
    for _ in range(3):                      # 括号套括号时由内往外剥
        t2 = _PAREN_RE.sub(_paren_to_tag, t)
        if t2 == t:
            break
        t = t2
    t = _LOUD_TAG_RE.sub(" ", t)
    t = _EMOJI_RE.sub("", t)
    t = _MD_RE.sub("", t)
    # 「过来 坐好」→「过来，坐好」：没有标点嗓子就不换气
    t = _CJK_SPACE_RE.sub("，", t)
    # 标签只留前 MAX_TAGS 个，多了就是在演
    n = 0

    def _cap(m: re.Match) -> str:
        nonlocal n
        n += 1
        return m.group(0) if n <= MAX_TAGS or SING_TAG_RE.fullmatch(m.group(0)) else " "
    t = ANY_TAG_RE.sub(_cap, t)
    lines = [re.sub(r"[ \t]{2,}", " ", x).strip(" \t，,") for x in t.split("\n")]
    # 只剩标签、没有一个字的行（整行都是动作）丢掉
    lines = [x for x in lines if ANY_TAG_RE.sub("", x).strip(" ，,。.…")]
    return "\n".join(lines)


def prepare_text(reply: str) -> str:
    """把他的多条气泡合成一段给合成器：‖ 和空行都当停顿，动作括号等不念。"""
    t = speakable(reply)
    t = re.sub(r"\n\s*\n+", "\n", t).strip()
    return t[:MAX_CHARS]


async def synth(reply: str, *, singing: bool | None = None, timeout: float = 60.0,
                previous_text: str = "", next_text: str = "") -> bytes:
    """合成一条语音。返回 Ogg/Opus 字节；任何失败都抛出去，由调用方退回文字。"""
    if not configured():
        raise RuntimeError("ElevenLabs 没配：需要 ELEVEN_API_KEY 和 ELEVEN_VOICE_ID")
    text = prepare_text(reply)
    if not text:
        raise ValueError("没有可合成的文字")
    if singing is None:
        singing = wants_singing(text)
    ctx = {"previous_text": previous_text, "next_text": next_text}
    try:
        return await _post(text, MODEL_ID, singing, timeout, ctx)
    except _Rejected as e:
        if MODEL_ID == FALLBACK_MODEL_ID:
            raise RuntimeError(str(e)) from None
        # v4 不认（没开通、参数不对、暂时不可用）→ 同一副嗓子退回 v3。日志里留着原因。
        logger.warning("%s 合成失败，退回 %s：%s", MODEL_ID, FALLBACK_MODEL_ID, e)
        try:
            return await _post(text, FALLBACK_MODEL_ID, singing, timeout)
        except _Rejected as e2:
            raise RuntimeError(f"{MODEL_ID}：{e}；{FALLBACK_MODEL_ID}：{e2}") from None


class _Rejected(Exception):
    """ElevenLabs 回了非 200 或空音频。"""


def _settings(model: str, singing: bool) -> dict:
    stability = 0.3 if singing else STABILITY
    if model.startswith("eleven_v4"):
        # v4 只认 stability 和 similarity_boost（官方文档：style/speed 不适用于 v4）
        return {"stability": stability, "similarity_boost": 0.8}
    # v3：唱歌时放开一点；说话按 STABILITY
    return {"stability": stability, "similarity_boost": 0.8,
            "style": 0.4 if singing else 0.2, "use_speaker_boost": True}


async def _post(text: str, model: str, singing: bool, timeout: float,
                ctx: dict | None = None) -> bytes:
    url = f"{BASE_URL}/v1/text-to-speech/{VOICE_ID}"
    body = {"text": text, "model_id": model, "voice_settings": _settings(model, singing)}
    # 一段一段念的时候把前后句递过去，语气才接得上（参考文第一节）。
    # 只给 v4：参考文说 v3 给了会报错。
    if model.startswith("eleven_v4"):
        for k, v in (ctx or {}).items():
            if v:
                body[k] = v[:1000]
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            url,
            params={"output_format": OUTPUT_FORMAT},
            headers={"xi-api-key": API_KEY, "accept": "audio/ogg"},
            json=body,
        )
    if r.status_code != 200:
        raise _Rejected(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
    if not r.content or len(r.content) < 200:
        # 0 字节的语音条比没有更坏：发出去她那边是个放不出来的空条
        raise _Rejected("ElevenLabs 返回了空音频")
    return r.content
