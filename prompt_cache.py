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


# GLM 的「深度思考」档位。GLM-4.5~5.2 支持 thinking={"type":"disabled"} 直接关掉；
# GLM-5.3 起强制思考，传 disabled 会报 1210（cannot be disabled; please use low,
# high, or max）。旧逻辑一遇到 thinking 报错就把参数整个丢掉，结果思考不受限、
# 把 max_tokens 全烧在隐藏推理上，正文返回空字符串 → 上层判成 model_empty。
# 现在改成：被拒绝就自动降到最低档 low，并按模型记住，不再每轮白试。
_THINKING_MIN_LEVEL = "low"
_THINKING_LEVELS = ("disabled", "low", "high", "max")
_thinking_mode: dict[str, str] = {}


def thinking_request(model: str | None, want_off: bool = True) -> dict[str, Any] | None:
    """这次调用该带的 thinking 字段；None 表示不带（用模型自己的默认）。"""
    if not want_off:
        return None
    mode = _thinking_mode.get(model or "", "disabled")
    if mode == "none":
        return None
    return {"thinking": {"type": mode}}


def note_thinking_error(model: str | None, error: object) -> bool:
    """记下某模型对 thinking 的反应。返回 True＝已换档位，值得重试一次。"""
    text = str(error).lower()
    if "thinking" not in text:
        return False  # 不是这个参数的锅，交给调用方原样抛出
    key = model or ""
    cannot_disable = "cannot be disabled" in text or "low, high, or max" in text
    if cannot_disable and _thinking_mode.get(key) != _THINKING_MIN_LEVEL:
        _thinking_mode[key] = _THINKING_MIN_LEVEL  # 5.3 这类：关不掉就开最低档
    else:
        _thinking_mode[key] = "none"  # 压根不认这个字段：以后都不带
    return True


def preset_thinking_level(model: str | None, level: str) -> None:
    """让 env 直接指定档位（OMBRE_GLM_THINKING=low/high/max）。"""
    if level in _THINKING_LEVELS:
        _thinking_mode[model or ""] = level


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


def append_volatile_context(messages: list[dict], context: str) -> list[dict]:
    """把每轮都在变的东西放到**所有消息之后**，作为独立的一条。

    为什么不能像 inject_volatile_context 那样塞进最后一条 user 里：
    存进历史的是原文，塞过的是「动态背景 + 原文」。同一条消息这一轮和下一轮
    渲染出的字节就不一样，缓存是前缀匹配的，从那个位置往后全部失效——
    历史对话**永远进不了缓存**。（NyraSeithhh/cache 的第 2 条铁律：
    会变的全部排到断点之后。）

    放在末尾就没这个问题：它只存在于当轮请求里，下一轮不会重现，
    而它之前的所有消息逐字节不变。
    """
    copied = [dict(message) for message in messages]
    if not context:
        return copied
    copied.append({"role": "user", "content": context})
    return copied


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


def _completion_tokens(usage: Any) -> int:
    """输出 token 通常比输入贵好几倍，以前一个字都没记，算账时等于瞎了半只眼。"""
    def get(obj, key):
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    for key in ("completion_tokens", "output_tokens"):
        value = get(usage, key)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _bump(bucket: dict, prompt: int, cached: int, completion: int) -> None:
    bucket["requests"] = int(bucket.get("requests", 0) or 0) + 1
    bucket["hits"] = int(bucket.get("hits", 0) or 0) + (1 if cached > 0 else 0)
    bucket["prompt_tokens"] = int(bucket.get("prompt_tokens", 0) or 0) + prompt
    bucket["cached_tokens"] = int(bucket.get("cached_tokens", 0) or 0) + cached
    bucket["completion_tokens"] = int(bucket.get("completion_tokens", 0) or 0) + completion


def record_usage(
    usage: Any,
    channel: str,
    path: str | os.PathLike[str] | None = None,
    model: str = "",
) -> dict[str, Any] | None:
    """Persist aggregate token counts only; prompts and replies are never stored.

    ⚠️ 按来源和按模型分别记 token，不只是记次数。由来：她问「$5 怎么没的」，
    统计只答得出「4287 次请求」，答不出这些请求分别烧了多少、烧在哪个模型上——
    于是只能靠猜。参考 relay-cache-where-it-breaks §6：「哪一轮」和「哪个模型」
    对不上号，是判错的共同原因；记 usage 时把 model 一起记进去。"""
    values = cache_usage(usage)
    if values is None:
        return None
    prompt, cached = values
    completion = _completion_tokens(usage)
    target = _stats_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        lock_path = target.with_suffix(target.suffix + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            os.chmod(lock_path, 0o600)
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            data = read_stats(target)
            _bump(data, prompt, cached, completion)
            _bump(data.setdefault("channels", {}).setdefault(channel, {}),
                  prompt, cached, completion)
            if model:
                _bump(data.setdefault("models", {}).setdefault(model, {}),
                      prompt, cached, completion)
            data["last"] = {
                "channel": channel,
                "model": model,
                "completion_tokens": completion,
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
