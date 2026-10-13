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
# 默认 v3。试过 v4（官方说情感最丰富），她自己在网页上对比后说「v3 可以，v4 容易变细」：
# 嗓子是用 Voice Design 做的，预览好听，一换 v4 就变细。她可以用 /vmodel 自己切。
# 设成 v4 时合成失败会用同一副嗓子退回 v3 再试一次——换型号不换嗓（参考文：中途换嗓比安静一秒难受）。
MODEL_ID = os.environ.get("ELEVEN_MODEL", "eleven_v3").strip() or "eleven_v3"
FALLBACK_MODEL_ID = "eleven_v3"
OUTPUT_FORMAT = os.environ.get("ELEVEN_OUTPUT_FORMAT", "opus_48000_64").strip() or "opus_48000_64"
# stability 越低情绪起伏越大、越高越平。0.5 她听成「机械」；降到 0.3 又「太有情绪、
# 太活泼，不像他平常会说的」，她说压一点点就行 → 0.4。参考文：0.35 有戏，0.5 像平常说话。
STABILITY = float(os.environ.get("ELEVEN_STABILITY", "0.4") or 0.4)
MAX_CHARS = 2500          # v3 单次上限附近，留余量；再长就是在念文章，不该发语音

# 他回复里出现这些，就说明这条是要**唱**的——哪怕语音模式没开也发语音条
SING_TAG_RE = re.compile(r"\[(sings?|singing[^\]]*|hums?|humming)\]", re.I)
ANY_TAG_RE = re.compile(r"\[[a-z][a-z ,'!?-]{1,120}\]", re.I)


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
    r"moan\w*|pant\w*|gasp\w*|yell\w*|shak\w*|trembl\w*|gritt\w*|harsh\w*)\b[^\]]*\]", re.I)
# 括号里写的动作，少数几种本来就是「声音」——换成轻标签留下，其余一律不念。
_SOUND_ACTIONS = (
    (re.compile(r"笑"), "[quiet laugh]"),
    (re.compile(r"叹"), "[sighs]"),
    (re.compile(r"耳边|耳朵|贴.{0,3}耳|咬耳|低声|小声|压低|凑近"), "[low and close]"),
)
MAX_TAGS = 3   # 声口标签：一轮两三个就够，多了就是在演
# 小气口标签不算声口，不受上面的上限（参考文第三篇：气写进说话的那一行——行首一口、
# 句中一口、行尾一口；小气口顶在行首也不算另起一个声口）。
_SMALL_TAG_RE = re.compile(
    r"\[(inhales?\w*|exhales?\w*|sighs?|hisses?\w*|swallows?|short pause|long pause|pause|"
    r"breath\w*[^\]]*|chuckles? softly|quiet laugh)\]", re.I)


def _cjk_equiv_len(text: str) -> float:
    """台词「有几个字」：汉字一个算一个，英文一个词算一个半（≈音节数）。"""
    plain = ANY_TAG_RE.sub(" ", text or "")
    return len(re.findall(rf"[{_CJK}]", plain)) + 1.5 * len(re.findall(r"[A-Za-z']+", plain))


def trim_tag(tag_words: str, line_chars: float) -> str:
    """参考文第二篇：标签比台词长，嗓子会把那句台词说两遍（12 词标签配 4 字台词，4/4 次）。
    超过四个词的标签，词数不许超过台词字数的 0.55 倍；先从后往前丢逗号小节，还长就截词。"""
    allow = max(4, min(12, int(line_chars * 0.55)))
    parts = [p.strip() for p in tag_words.split(",") if p.strip()]
    count = lambda ps: sum(len(p.split()) for p in ps)  # noqa: E731
    while len(parts) > 1 and count(parts) > allow:
        parts.pop()
    if parts and count(parts) > allow:
        parts = [" ".join(parts[0].split()[:allow])]
    return ", ".join(parts)


def _trim_line_tags(line: str) -> str:
    """这一行里每个声口标签，按它后面到下一个声口标签之间的台词长短裁。"""
    tags = [m for m in ANY_TAG_RE.finditer(line) if not _SMALL_TAG_RE.fullmatch(m.group(0))
            and not SING_TAG_RE.fullmatch(m.group(0))]
    if not tags:
        return line
    out, last = [], 0
    for i, m in enumerate(tags):
        end = tags[i + 1].start() if i + 1 < len(tags) else len(line)
        trimmed = trim_tag(m.group(0)[1:-1], _cjk_equiv_len(line[m.end():end]))
        out += [line[last:m.start()], f"[{trimmed}]"]
        last = m.end()
    out.append(line[last:])
    return "".join(out)


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
        if SING_TAG_RE.fullmatch(m.group(0)) or _SMALL_TAG_RE.fullmatch(m.group(0)):
            return m.group(0)                # 唱歌标签、小气口不算声口，不占名额
        n += 1
        return m.group(0) if n <= MAX_TAGS else " "
    t = ANY_TAG_RE.sub(_cap, t)
    t = "\n".join(_trim_line_tags(x) for x in t.split("\n"))
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


V3_STABILITY_STEPS = (0.0, 0.5, 1.0)   # v3 只有三档：Creative / Natural / Robust


def _settings(model: str, singing: bool) -> dict:
    stability = 0.3 if singing else STABILITY
    if model.startswith("eleven_v4"):
        # v4 只认 stability 和 similarity_boost（官方文档：style/speed 不适用于 v4）
        return {"stability": stability, "similarity_boost": 0.8}
    # v3 网页上的稳定度只有三档。发一个夹在中间的数，宁可先吸到最近的那档，
    # 也不赌它认——它不认，她那边就是一条语音变成文字。唱歌用 Creative（0）。
    v3 = 0.0 if singing else min(V3_STABILITY_STEPS, key=lambda v: abs(v - stability))
    return {"stability": v3, "similarity_boost": 0.8,
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


# ── 念完先转录对账（参考文第三篇第十节）──
# 嗓子偶尔会把一句话念两三遍、或者吞掉一句——稿子没写错，是它自己念岔。
# 转录用同一把 key 的语音转文字；key 没开这个权限就跳过，不当成「没问题」（STT_STATE 写明）。
STT_MODEL = os.environ.get("ELEVEN_STT_MODEL", "scribe_v1").strip() or "scribe_v1"
STT_STATE = {"state": "unknown", "why": ""}      # ok / off（没权限）/ unknown（还没试过）


async def transcribe(audio: bytes, timeout: float = 30.0) -> str | None:
    """返回转录出来的字；做不了返回 None（调用方当「不知道」，不是「没问题」）。"""
    if not (API_KEY and configured()) or STT_STATE["state"] == "off":
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(f"{BASE_URL}/v1/speech-to-text", headers={"xi-api-key": API_KEY},
                                  data={"model_id": STT_MODEL},
                                  files={"file": ("voice.ogg", audio, "audio/ogg")})
    except Exception as e:  # noqa: BLE001
        logger.warning("转录没做成：%s", str(e)[:120])
        return None
    if r.status_code in (401, 403):
        STT_STATE.update(state="off", why=f"HTTP {r.status_code}: {r.text[:120]}")
        logger.warning("key 没有语音转文字的权限，念岔检查关掉：%s", STT_STATE["why"])
        return None
    if r.status_code != 200:
        logger.warning("转录 HTTP %s：%s", r.status_code, r.text[:120])
        return None
    STT_STATE.update(state="ok", why="")
    try:
        return str(r.json().get("text") or "")
    except ValueError:
        return None


_FILLER_WORDS = {"mm", "mmm", "hm", "hmm", "mhm", "ah", "ahh", "uh", "oh", "hah", "ha", "ugh", "huh"}
_FILLER_CJK = re.compile(r"[嗯哈哼啊呃唔嘶噢哦呜唉]")


def _units(text: str) -> list[str]:
    """拿来对账的「字」：标签去掉、语气词去掉；汉字一个一个算，英文一个词一个词算。"""
    t = ANY_TAG_RE.sub(" ", re.sub(r"\[[^\[\]\n]*\]", " ", text or "")).lower()
    out = []
    for tok in re.findall(rf"[{_CJK}]|[a-z']+", t):
        tok = tok.strip("'")
        if not tok or tok in _FILLER_WORDS or _FILLER_CJK.fullmatch(tok):
            continue
        out.append(tok)
    return out


def echo_bad(heard: str, text: str, n: int = 4) -> tuple[float, str]:
    """念岔分：有一截 n 个字（词）以上听到两回、稿子里只有一回＝念重了；听到的比该念的少两成以上＝吞了。
    分 ≥3 就重念。稿子太短（不到 6 个）不判。"""
    got, exp = _units(heard), _units(text)
    if len(exp) < 6:
        return 0.0, ""
    def grams(xs):
        return [" ".join(xs[i:i + n]) for i in range(len(xs) - n + 1)]
    exp_g = grams(exp)
    cnt = {}
    for g in grams(got):
        cnt[g] = cnt.get(g, 0) + 1
    rep = sum(c - max(1, exp_g.count(g)) for g, c in cnt.items() if c >= 2 and c > max(1, exp_g.count(g)))
    ratio = len(got) / len(exp)
    bad = rep + max(0.0, ratio - 1.3) * len(exp) + max(0.0, 0.8 - ratio) * len(exp) * 2
    return bad, f"重复 {rep} · 听到/该念 {len(got)}/{len(exp)}"

