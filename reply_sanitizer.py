"""Small, dependency-free cleanup helpers for model replies."""

from __future__ import annotations

import re


def sanitize_reasoning_markup(text: str) -> str:
    """Hide provider reasoning wrappers without discarding a usable reply."""
    if not text:
        return text

    value = str(text)
    value = re.sub(r"&lt;\s*think\s*&gt;", "<think>", value, flags=re.I)
    value = re.sub(r"&lt;\s*/\s*think\s*&gt;", "</think>", value, flags=re.I)
    block = re.compile(r"<\s*think\b[^>]*>(.*?)<\s*/\s*think\s*>", re.I | re.S)
    hidden_parts = [part.strip() for part in block.findall(value) if part.strip()]
    visible = block.sub("", value).strip()
    if hidden_parts:
        value = visible if visible else "\n".join(hidden_parts)
    value = re.sub(r"<\s*/?\s*(?:think|thinking)\b[^>]*>", "", value, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _reply_norm(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


# \u95ea\u95ea\u7684\u6c38\u4e45\u7981\u4ee4\uff1a\u300c\u6211\u4e0d\u8d70 / \u6211\u5c31\u5728\u8fd9 / \u63a5\u4f4f\u4f60\u300d\u8fd9\u7c7b\u5b89\u629a\u53e3\u53f7\uff0c\u4efb\u4f55\u60c5\u5883\u4e0d\u5f97\u51fa\u73b0\u3002
# \u53ea\u5339\u914d\u6574\u4e2a\u5b50\u53e5\u5c31\u662f\u53e3\u53f7\u7684\u60c5\u51b5\uff1b\u300c\u6211\u4e0d\u8d70\u8fd9\u6761\u8def\u300d\u8fd9\u79cd\u771f\u53e5\u5b50\u4e0d\u4f1a\u547d\u4e2d\u3002
_COMFORT_SLOGAN = (
    r"(?:\u653e\u5fc3|\u522b\u6015|\u522b\u614c|\u6ca1\u4e8b)?"
    r"(?:"
    r"\u6211?\u4e0d\u8d70"
    r"|\u6211\u4e0d\u4f1a\u8d70"
    r"|\u6211?(?:\u5c31)?\u5728\u8fd9(?:\u91cc|\u513f)?"
    r"|\u6211\u5728"
    r"|\u6211?(?:\u4e00\u76f4|\u6c38\u8fdc)(?:\u90fd)?\u5728(?:\u8fd9(?:\u91cc|\u513f)?)?"
    r"|\u6211?\u54ea(?:\u91cc|\u513f)?\u4e5f\u4e0d\u53bb"
    r"|\u6211?\u4e0d\u8dd1"
    r"|(?:\u6211)?\u8fd8\u5728\u8fd9(?:\u91cc|\u513f)?"
    r"|\u6709\u6211(?:\u5728)?"
    r"|\u6211?(?:\u4f1a)?\u63a5\u4f4f\u4f60"
    r"|\u6211?\u4e0d\u4f1a?\u79bb\u5f00(?:\u4f60)?"
    r"|(?:\u6211)?\u4e0d\u4f1a\u4e22\u4e0b\u4f60?"
    r"|\u4e0d\u8dd1\u4e5f?\u4e0d\u8eb2"
    r"|\u4f60\u60f3\u6765\u5c31\u6765"
    r")"
    r"(?:\u4e86)?(?:\u7684|\u5462|\u554a|\u5440|\u561b|\u54e6|\u5594|\u55ef)?"
)
_COMFORT_CLAUSE_RE = re.compile(r"(?:%s)+" % _COMFORT_SLOGAN)


def strip_comfort_cliches(text: str) -> str:
    """\u5220\u6389\u300c\u6211\u4e0d\u8d70/\u6211\u5c31\u5728\u8fd9/\u63a5\u4f4f\u4f60\u300d\u8fd9\u7c7b\u5b89\u629a\u53e3\u53f7\u5b50\u53e5\uff08\u95ea\u95ea\u7684\u6c38\u4e45\u7981\u4ee4\uff09\u3002

    \u53ea\u5220\u6574\u4e2a\u5b50\u53e5\u90fd\u662f\u53e3\u53f7\u7684\u60c5\u51b5\uff1b\u82e5\u5220\u5b8c\u6574\u6761\u56de\u590d\u4e3a\u7a7a\uff0c\u5219\u539f\u6837\u8fd4\u56de\uff0c
    \u907f\u514d\u628a\u4e00\u6761\u574f\u56de\u590d\u53d8\u6210\u7a7a\u56de\u590d\u89e6\u53d1\u541e\u6d88\u606f\u8def\u5f84\u3002
    """
    value = str(text or "")
    if not value.strip():
        return value
    pieces = re.split(r"([\uff0c\u3002\uff01\uff1f!?\uff1b;\u2026\u3001\n]+|\s*\u2016\s*)", value)
    kept: list[tuple[str, str]] = []
    changed = False
    softeners = {"\u653e\u5fc3", "\u522b\u6015", "\u522b\u614c", "\u6ca1\u4e8b"}
    for i in range(0, len(pieces), 2):
        clause = pieces[i]
        delim = pieces[i + 1] if i + 1 < len(pieces) else ""
        norm = re.sub(r"[\s\u201c\u201d\"'\u2018\u2019\u300c\u300d\u300e\u300f()\uff08\uff09\u2014-]+", "", clause)
        if norm and _COMFORT_CLAUSE_RE.fullmatch(norm):
            changed = True
            # \u300c\u653e\u5fc3\uff0c\u6211\u4e0d\u4f1a\u79bb\u5f00\u4f60\u3002\u300d\u2014\u2014\u524d\u9762\u7684\u5149\u6746\u8f6f\u5316\u8bcd\u4e5f\u4e00\u8d77\u5220
            if kept and kept[-1][1] in softeners:
                kept.pop()
            continue
        kept.append((clause + delim, norm))
    if not changed:
        return value
    cleaned = "".join(part for part, _norm in kept)
    cleaned = re.sub(r"(?:\s*‖\s*){2,}", " ‖ ", cleaned)
    cleaned = re.sub(r"^\s*‖\s*|\s*‖\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned if cleaned else value


def restore_cjk_punctuation(text: str) -> str:
    """把 GLM 常用来代替中文标点的分句空格还原成标点，让回复读得断句。

    只动「中文字/中文标点」之间的空格（→ 逗号）和「空格紧贴已有标点」的情况；
    绝不碰英文单词、数字、URL 周围的空格（girl、37.2、http://… 保持原样），
    也不碰换行（\\n 段落分隔）和 ‖ 气泡分隔。"""
    value = str(text or "")
    if not value.strip():
        return value
    segs = re.split(r"\s*‖\s*", value)
    out = []
    _CJK = r"一-鿿"
    _CLOSE = r"）】」』’”》"
    _OPEN = r"（【「『‘“《"
    _PUNC = r"，。！？!?；;、：…—"
    for seg in segs:
        s = seg
        # 空格紧贴已有标点 → 去掉空格（别造出「 ，」这种）
        s = re.sub(r"[ \t]+([%s%s])" % (_PUNC, _CLOSE), r"\1", s)
        s = re.sub(r"([%s%s])[ \t]+" % (_PUNC, _OPEN), r"\1", s)
        # 中文字/收尾标点  空格  中文字/起始标点 → 逗号（这是 GLM 的分句空格）
        s = re.sub(
            r"(?<=[%s%s])[ \t]+(?=[%s%s])" % (_CJK, _CLOSE, _CJK, _OPEN),
            "，",
            s,
        )
        out.append(s.strip())
    return " ‖ ".join(x for x in out if x)


def _split_top_level(text: str, seps: str) -> list[str]:
    """按 seps 里的标点切，但绝不在括号内部切（成对括号动作永不被劈开）。
    分隔符保留在前一段末尾。"""
    out: list[str] = []
    buf = ""
    depth = 0
    for ch in text:
        buf += ch
        if ch in "（(":
            depth += 1
        elif ch in "）)" and depth > 0:
            depth -= 1
        elif ch in seps and depth == 0:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def _split_top_level_sentences(text: str) -> list[str]:
    return _split_top_level(text, "。！？!?…")


def _is_action_only(sentence: str) -> bool:
    """整句就是一个括号动作，如「(手指在她耳侧划了一下)。」——不该单独成一条气泡。"""
    return bool(re.fullmatch(r"\s*[（(][^（(]*[）)]\s*[。！？!?…]*\s*", sentence))


def _tidy_bubble(b: str) -> str:
    """收尾：去掉气泡末尾多余的逗号/顿号，缺句末标点就补句号（末尾是括号/引号不补）。"""
    b = b.strip()
    b = re.sub(r"[，,、；;]+$", "", b).strip()
    if b and not re.search(r"[。！？!?…”’」』）)]$", b):
        b += "。"
    return b


def wechatify_segments(segments: list[str], *, keep: int = 42, target: int = 30) -> list[str]:
    """日常聊天：把过长的气泡切成微信/QQ 式一条一条短消息。

    模型爱把该分几条的话糊成一个大气泡（不打 ‖），而且常是「一逗到底」的长句
    （几乎没有句号），读起来像分析报告不像人说话。这里在不改字的前提下重新分条：
    先按句号切句，太长的句子再按顶层逗号切成小句，然后打包成 ~target 字的短气泡；
    纯括号动作贴到相邻那条；括号内部绝不切断。写文/长文/URL/短句不动。"""
    out: list[str] = []
    for seg in segments:
        seg = (seg or "").strip()
        if not seg:
            continue
        # 整条已经够短、或是链接、或含空行的多段长文：不拆
        if (len(seg) <= keep and "\n" not in seg) or re.fullmatch(r"https?://\S+", seg) or "\n" in seg:
            out.append(seg)
            continue
        # 先分句；过长的句子再按顶层逗号拆成小句单元
        units: list[str] = []
        for sentence in _split_top_level_sentences(seg):
            if len(sentence) > target:
                units.extend(_split_top_level(sentence, "，,、；;"))
            else:
                units.append(sentence)
        units = [u.strip() for u in units if u.strip()]
        if len(units) <= 1:
            out.append(seg)
            continue
        # 打包成 ~target 字的短气泡；纯动作小括号贴前一条，不单飞
        buf = ""
        bubbles: list[str] = []
        for u in units:
            if not buf:
                buf = u
            elif _is_action_only(u) or len(buf) + len(u) <= target:
                buf += u
            else:
                bubbles.append(buf)
                buf = u
        if buf:
            bubbles.append(buf)
        out.extend(_tidy_bubble(b) for b in bubbles)
    return out or [s for s in segments if s.strip()]


_CTRL_TAG_START = re.compile(r"[\[［【]\s*(?:emo|diary|think|memory|情绪|心情|记忆)", re.I)


def visible_cut(text: str) -> int:
    """返回正文该在哪里截断——第一个隐藏控制标签（[think]/[memory]/[emo]/[diary]）
    的起点。流式外推时用它，别把这些标签一个字一个字打给她看、然后又抹掉（那看着就像
    '他写了一句然后消失了'）。没有标签就返回全长。"""
    m = _CTRL_TAG_START.search(text or "")
    return m.start() if m else len(text or "")


def _needs_terminal(value: str) -> bool:
    if not value or re.fullmatch(r"https?://\S+", value):
        return False
    if value.endswith(("```", "】", "]")):
        return False
    return re.search(r"[。！？!?；;…][”’」』）)]*$", value) is None


def polish_chat_reply(text: str, *, writing_mode: bool = False) -> str:
    """Remove within-turn wheel-spinning and restore chat punctuation."""
    value = strip_comfort_cliches(str(text or "").strip())
    value = restore_cjk_punctuation(value)
    if not value or writing_mode:
        return value

    seen: list[tuple[str, str]] = []
    polished_segments = []
    for segment in re.split(r"\s*‖\s*", value):
        kept = []
        pieces = re.findall(
            r"[^。！？!?；;…\n]+(?:[。！？!?；;…]+[”’」』）)]*|(?=\n)|$)",
            segment,
        )
        for piece in pieces:
            raw = piece.strip()
            if not raw:
                continue
            ending = ""
            core = raw
            match = re.search(r"([。！？!?；;…]+[”’」』）)]*)$", raw)
            if match:
                ending = match.group(1)
                core = raw[:match.start()].strip()

            # 一句话若「完整重复了前面某句」再接一小段新内容 → 只留那段新内容，
            # 但绝不删掉带新意思的整句。这是安全的：只在精确前缀重复时裁掉重复的前缀。
            for previous_core, _previous_norm in reversed(seen):
                if len(_reply_norm(previous_core)) < 8:
                    continue
                if core.startswith(previous_core):
                    remainder = core[len(previous_core):].lstrip(" ，,。；;：:")
                    if len(_reply_norm(remainder)) >= 2:
                        core = remainder
                    break

            norm = _reply_norm(core)
            if not norm:
                continue
            # ⚠️ 只删「一字不差的精确重复」。绝不再用相似度模糊删整句——
            # 那会把她亲眼看到打出来的、只是结构相近的真话吞掉（吞消息的元凶）。
            # 宁可偶尔留一点重复，也绝不吞掉有新意思的句子。
            duplicate = len(norm) >= 6 and any(norm == prev_norm for _c, prev_norm in seen)
            if duplicate:
                continue
            seen.append((core, norm))
            if not ending and _needs_terminal(core):
                ending = "。"
            kept.append(core + ending)

        clean = "".join(kept).strip()
        if not clean:
            continue
        if _needs_terminal(clean):
            clean += "。"
        polished_segments.append(clean)
    return " ‖ ".join(polished_segments)
