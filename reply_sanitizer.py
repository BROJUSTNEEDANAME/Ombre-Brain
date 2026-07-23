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


def _needs_terminal(value: str) -> bool:
    if not value or re.fullmatch(r"https?://\S+", value):
        return False
    if value.endswith(("```", "】", "]")):
        return False
    return re.search(r"[。！？!?；;…][”’」』）)]*$", value) is None


def polish_chat_reply(text: str, *, writing_mode: bool = False) -> str:
    """Remove within-turn wheel-spinning and restore chat punctuation."""
    value = str(text or "").strip()
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
