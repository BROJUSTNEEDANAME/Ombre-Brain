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
