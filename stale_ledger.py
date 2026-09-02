# -*- coding: utf-8 -*-
"""被矛盾检测沉底的记忆的账本。

存在的理由：自动沉底如果没有账、不能撤销，就是一个会悄悄吞记忆的黑箱。
她必须能看见「什么被沉底了、为什么」，并且一句话就能恢复。
只记元信息（ID/标题/理由/时间），不存记忆正文。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

MAX_ENTRIES = 200


def ledger_path(path: str | os.PathLike[str] | None = None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get("OMBRE_BUCKETS_DIR", "./buckets")) / "stale_ledger.json"


def load(path=None) -> list[dict]:
    try:
        data = json.loads(ledger_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def record(entries: list[dict], path=None) -> list[dict]:
    """追加几笔。最新的排最前面，只留最近 MAX_ENTRIES 条。"""
    if not entries:
        return load(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fresh = [{
        "old_id": str(e.get("old_id") or ""),
        "old_name": str(e.get("old_name") or "")[:60],
        "new_id": str(e.get("new_id") or ""),
        "reason": str(e.get("reason") or "")[:200],
        "confidence": round(float(e.get("confidence") or 0), 2),
        "at": now,
        "undone": False,
    } for e in entries if e.get("old_id")]
    data = (fresh + load(path))[:MAX_ENTRIES]
    target = ledger_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return data


def mark_undone(old_id: str, path=None) -> bool:
    """标记某条已经被恢复。找不到或已经恢复过返回 False。"""
    data = load(path)
    hit = False
    for e in data:
        if e.get("old_id") == old_id and not e.get("undone"):
            e["undone"] = True
            hit = True
    if hit:
        target = ledger_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    return hit


def pending(path=None, limit: int = 10) -> list[dict]:
    """还没被恢复的，最新的在前。"""
    return [e for e in load(path) if not e.get("undone")][:limit]
