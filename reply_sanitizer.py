"""Small, dependency-free cleanup helpers for model replies."""

from __future__ import annotations

import re
from difflib import SequenceMatcher


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

            # If a new sentence repeats the whole previous sentence before
            # adding one new clause, keep only that new clause.
            for previous_core, _previous_norm in reversed(seen):
                if len(_reply_norm(previous_core)) < 8:
                    continue
                if core.startswith(previous_core):
                    remainder = core[len(previous_core):].lstrip(" ，,。；;：:")
                    if len(_reply_norm(remainder)) >= 4:
                        core = remainder
                    break

            norm = _reply_norm(core)
            if not norm:
                continue
            duplicate = False
            if len(norm) >= 8:
                for _previous_core, previous_norm in seen:
                    if norm == previous_norm:
                        duplicate = True
                        break
                    similarity = SequenceMatcher(
                        None, norm, previous_norm, autojunk=False
                    ).ratio()
                    length_ratio = max(len(norm), len(previous_norm)) / max(
                        1, min(len(norm), len(previous_norm))
                    )
                    if similarity >= 0.87 and length_ratio <= 1.35:
                        duplicate = True
                        break
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
