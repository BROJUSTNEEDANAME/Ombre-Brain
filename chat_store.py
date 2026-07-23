"""Small, process-local persistence layer for cross-client chat history."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reply_sanitizer import sanitize_reasoning_markup


DISPLAY_TZ = ZoneInfo("America/Los_Angeles")
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_utc(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except ValueError:
            pass
    return utc_now()


def display_parts(utc_value: str | None) -> tuple[str, str]:
    parsed = datetime.fromisoformat(normalize_utc(utc_value).replace("Z", "+00:00"))
    local = parsed.astimezone(DISPLAY_TZ)
    return f"{local.year}-{local.month}-{local.day}", local.strftime("%H:%M")


def _legacy_timestamp(message: dict) -> str:
    dk = str(message.get("dk") or "").strip()
    clock = str(message.get("t") or "").strip()
    try:
        local = datetime.strptime(f"{dk} {clock}", "%Y-%m-%d %H:%M").replace(tzinfo=DISPLAY_TZ)
        return local.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except ValueError:
        return utc_now()


def _image_digest(value) -> str:
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]


def _legacy_key(message: dict) -> str:
    payload = {
        "side": str(message.get("side") or ""),
        "text": str(message.get("text") or ""),
        "t": str(message.get("t") or ""),
        "dk": str(message.get("dk") or ""),
        "img": _image_digest(message.get("img")),
        "think": str(message.get("think") or ""),
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ensure_message_ids(log: list) -> list[dict]:
    """Upgrade legacy rows deterministically without merging repeated text."""
    counts: dict[str, int] = {}
    out: list[dict] = []
    next_seq = 1
    for raw in log if isinstance(log, list) else []:
        if not isinstance(raw, dict):
            continue
        message = dict(raw)
        key = _legacy_key(message)
        if message.get("side") == "you" and message.get("text"):
            message["text"] = sanitize_reasoning_markup(message["text"])
        occurrence = counts.get(key, 0) + 1
        counts[key] = occurrence
        if not message.get("id"):
            digest = hashlib.sha256(f"{key}|{occurrence}".encode("utf-8")).hexdigest()[:32]
            message["id"] = f"legacy:{digest}"
        if not message.get("ts"):
            message["ts"] = _legacy_timestamp(message)
        else:
            message["ts"] = normalize_utc(message["ts"])
        try:
            message["seq"] = int(message.get("seq") or next_seq)
        except (TypeError, ValueError):
            message["seq"] = next_seq
        next_seq = max(next_seq + 1, message["seq"] + 1)
        message.setdefault("source", "legacy")
        out.append(message)
    return out


def order_messages(log: list) -> list[dict]:
    """Return stable chronological order without discarding any message."""
    messages = ensure_message_ids(log)

    def key(item: tuple[int, dict]) -> tuple[datetime, int, int]:
        index, message = item
        try:
            timestamp = datetime.fromisoformat(str(message.get("ts") or "").replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp = timestamp.astimezone(timezone.utc)
        except (TypeError, ValueError):
            timestamp = datetime.max.replace(tzinfo=timezone.utc)
        try:
            sequence = int(message.get("seq") or index)
        except (TypeError, ValueError):
            sequence = index
        return timestamp, sequence, index

    return [message for _, message in sorted(enumerate(messages), key=key)]


def merge_logs(existing: list, incoming: list) -> list[dict]:
    """Merge by stable ID; identical text with different IDs remains distinct."""
    current = ensure_message_ids(existing)
    added = ensure_message_ids(incoming)
    positions = {str(message["id"]): i for i, message in enumerate(current)}
    next_seq = max((int(m.get("seq") or 0) for m in current), default=0) + 1
    for message in added:
        message_id = str(message["id"])
        if message_id in positions:
            old = current[positions[message_id]]
            safe = {k: v for k, v in message.items() if k not in {"id", "seq", "source", "ts", "side", "text"}}
            current[positions[message_id]] = {**old, **safe}
            continue
        message["seq"] = next_seq
        next_seq += 1
        positions[message_id] = len(current)
        current.append(message)
    return order_messages(current)


def make_message(
    message_id: str,
    side: str,
    text: str,
    *,
    source: str,
    timestamp: str | None = None,
    reply_to: str | None = None,
    extras: dict | None = None,
) -> dict:
    ts = normalize_utc(timestamp)
    dk, clock = display_parts(ts)
    message = {
        "id": message_id,
        "side": side,
        "text": text,
        "source": source,
        "ts": ts,
        "dk": dk,
        "t": clock,
    }
    if reply_to:
        message["reply_to"] = reply_to
    if extras:
        # Auxiliary UI state belongs to the same stable message. Keeping it here
        # prevents a server refresh from replacing a rich local bubble with a
        # text-only copy.
        for key in ("think", "recorded", "emotion", "diary"):
            value = extras.get(key)
            if value not in (None, "", []):
                message[key] = value
    return message


def new_legacy_request_id() -> str:
    return f"legacy-request:{uuid.uuid4()}"


def history_from_log(log: list, limit: int = 40) -> list[dict]:
    history = []
    for message in order_messages(log):
        text = str(message.get("text") or "").strip()
        if not text:
            continue
        if message.get("side") == "me":
            history.append({"role": "user", "content": text})
        elif message.get("side") == "you":
            history.append({"role": "assistant", "content": text})
    return history[-limit:]


def response_for(log: list, request_id: str) -> dict | None:
    replies = [m for m in ensure_message_ids(log) if m.get("reply_to") == request_id and m.get("side") == "you"]
    if not replies:
        return None
    segments = [str(m.get("text") or "") for m in replies if str(m.get("text") or "").strip()]
    last = replies[-1]
    return {
        "reply": "\n".join(segments),
        "segments": segments,
        "think": str(last.get("think") or ""),
        "recorded": list(last.get("recorded") or []),
        "emotion": str(last.get("emotion") or ""),
        "diary": str(last.get("diary") or ""),
        "message_id": str(request_id),
        "deduplicated": True,
    }


def _lock_for(path: str) -> threading.RLock:
    absolute = os.path.abspath(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(absolute, threading.RLock())


@contextmanager
def locked(path: str):
    with _lock_for(path):
        yield


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        data = {}
    log = order_messages(data.get("log") or [])
    return {"schema": 2, "log": log, "hist": history_from_log(log)}


def save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    log = order_messages(data.get("log") or [])[-400:]
    payload = {"schema": 2, "log": log, "hist": history_from_log(log)}
    fd, temporary = tempfile.mkstemp(prefix=".chat-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
