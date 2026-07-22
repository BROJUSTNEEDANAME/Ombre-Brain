"""Helpers for GLM's implicit prompt cache and cache-hit observability.

The stable-prefix layout is informed by https://github.com/NyraSeithhh/cache;
the implementation here is original and uses Z.AI's documented implicit cache.
"""

from __future__ import annotations

import json
import os
import re
import threading
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_WRITE_LOCK = threading.Lock()
_DEFAULT_USER_ID = "ombre-shanshan-primary"


def stable_user_id() -> str:
    """Return one non-sensitive, stable routing ID for the private conversation."""
    raw = os.environ.get("OMBRE_PROMPT_CACHE_USER_ID", _DEFAULT_USER_ID).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._:-]", "-", raw)[:128]
    return cleaned if len(cleaned) >= 6 else _DEFAULT_USER_ID


def is_zai_endpoint(base_url: str | None = None) -> bool:
    """Only send Z.AI-specific request fields to providers that document them."""
    url = (base_url or os.environ.get("LLM_BASE_URL", "")).lower()
    return "api.z.ai" in url or "open.bigmodel.cn" in url


def request_extra_body(
    existing: dict[str, Any] | None = None,
    *,
    base_url: str | None = None,
    thinking: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge optional GLM fields without overwriting caller-supplied values."""
    body = dict(existing or {})
    if is_zai_endpoint(base_url):
        body.setdefault("user_id", stable_user_id())
    if thinking:
        for key, value in thinking.items():
            body.setdefault(key, value)
    return body


def inject_volatile_context(messages: list[dict], context: str) -> list[dict]:
    """Put changing context immediately before the newest user content.

    Earlier messages are copied byte-for-byte so GLM can reuse their implicit
    prefix cache. The input list and its message objects are not mutated.
    """
    copied = [dict(message) for message in messages]
    if not context:
        return copied
    for index in range(len(copied) - 1, -1, -1):
        if copied[index].get("role") != "user":
            continue
        content = copied[index].get("content", "")
        if isinstance(content, list):
            copied[index]["content"] = [
                {"type": "text", "text": context + "\n\n"},
                *content,
            ]
        else:
            copied[index]["content"] = context + "\n\n" + str(content or "")
        break
    return copied


def cache_usage(usage: Any) -> tuple[int, int] | None:
    """Extract ``(prompt_tokens, cached_tokens)`` from SDK objects or dicts."""
    if usage is None:
        return None

    def get(value: Any, key: str, default: Any = None) -> Any:
        return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)

    prompt = int(get(usage, "prompt_tokens", 0) or 0)
    details = get(usage, "prompt_tokens_details")
    cached = int(get(details, "cached_tokens", 0) or 0) if details is not None else 0
    return prompt, cached


def _stats_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    buckets = os.environ.get("OMBRE_BUCKETS_DIR", "./buckets")
    return Path(buckets) / "prompt_cache_stats.json"


def read_stats(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    target = _stats_path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        data = {}
    prompt = int(data.get("prompt_tokens", 0) or 0)
    cached = int(data.get("cached_tokens", 0) or 0)
    data["hit_rate"] = round(cached / prompt * 100, 2) if prompt else 0.0
    return data


def record_usage(
    usage: Any,
    channel: str,
    path: str | os.PathLike[str] | None = None,
) -> dict[str, Any] | None:
    """Persist aggregate token counts only; prompts and replies are never stored."""
    values = cache_usage(usage)
    if values is None:
        return None
    prompt, cached = values
    target = _stats_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        lock_path = target.with_suffix(target.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            data = read_stats(target)
            data["requests"] = int(data.get("requests", 0) or 0) + 1
            data["hits"] = int(data.get("hits", 0) or 0) + (1 if cached > 0 else 0)
            data["prompt_tokens"] = int(data.get("prompt_tokens", 0) or 0) + prompt
            data["cached_tokens"] = int(data.get("cached_tokens", 0) or 0) + cached
            channels = data.setdefault("channels", {})
            item = channels.setdefault(channel, {"requests": 0, "hits": 0})
            item["requests"] = int(item.get("requests", 0) or 0) + 1
            item["hits"] = int(item.get("hits", 0) or 0) + (1 if cached > 0 else 0)
            data["last"] = {
                "channel": channel,
                "prompt_tokens": prompt,
                "cached_tokens": cached,
                "hit_rate": round(cached / prompt * 100, 2) if prompt else 0.0,
                "at": datetime.now(timezone.utc).isoformat(),
            }
            data["hit_rate"] = round(data["cached_tokens"] / data["prompt_tokens"] * 100, 2) if data["prompt_tokens"] else 0.0
            temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
            temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, target)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return data
