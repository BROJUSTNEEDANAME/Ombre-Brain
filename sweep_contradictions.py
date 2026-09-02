#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫一轮记忆库，把「被新记忆改写了的旧记忆」沉底。

    python3 sweep_contradictions.py            # 预演，只打印会动谁
    python3 sweep_contradictions.py --write    # 真的沉底
    python3 sweep_contradictions.py --days 3 --write

默认 dry-run。判断逻辑全在 contradiction.py（有测试），这里只负责跑腿：
从大脑取最近的记忆、给每条捞候选、问模型、把结果落到账本上。

判官用便宜模型（默认 glm-5.2），跟聊天用的那个无关——这是后台活儿，
不该花 Opus 的钱，也不该占她等回复的时间。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import contradiction as cd            # noqa: E402
import stale_ledger                   # noqa: E402

BRAIN = os.environ.get("OMBRE_BRAIN_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("OMBRE_BRAIN_TOKEN", "").strip()
JUDGE_MODEL = os.environ.get("OMBRE_JUDGE_MODEL", "glm-5.2")
TIMEOUT = 60


def _get(path: str):
    headers = {"Accept": "application/json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(BRAIN + path, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def recent_buckets(days: int) -> list[dict]:
    """最近 N 天新建的桶——只有新记忆才可能让旧的过时。"""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out = []
    for b in _get("/api/buckets"):
        meta = b if "created" in b else (b.get("metadata") or {})
        if str(meta.get("created") or "") >= cutoff and not cd.is_protected(b):
            out.append(b)
    return out


def _detail(bucket_id: str) -> dict:
    try:
        return _get("/api/bucket/" + urllib.parse.quote(bucket_id))
    except Exception:  # noqa: BLE001
        return {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="只看最近几天的新记忆")
    ap.add_argument("--write", action="store_true", help="真的沉底（默认只预演）")
    ap.add_argument("--max", type=int, default=cd.MAX_RETIRE_PER_SWEEP)
    args = ap.parse_args()

    try:
        fresh = recent_buckets(args.days)
    except urllib.error.HTTPError as e:
        print(f"[矛盾检测] 大脑拒绝了：HTTP {e.code}"
              + ("（403 = 缺 OMBRE_BRAIN_TOKEN）" if e.code == 403 else ""))
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[矛盾检测] 连不上大脑 {BRAIN}：{e}")
        return 1

    print(f"[矛盾检测] 最近 {args.days} 天有 {len(fresh)} 条新记忆")
    if not fresh:
        return 0

    from openai import AsyncOpenAI  # noqa: PLC0415
    llm = AsyncOpenAI(
        api_key=(os.environ.get("LLM_API_KEY") or os.environ.get("ZAI_API_KEY", "")).strip(),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.z.ai/api/paas/v4/"),
        timeout=60.0, max_retries=0)

    async def find_related(bucket: dict) -> list[dict]:
        """拿这条记忆的标题和正文去检索，捞出可能讲同一件事的旧记忆。"""
        meta = bucket.get("metadata") or bucket
        query = f"{meta.get('name') or ''} {bucket.get('content_preview') or ''}"[:120]
        hits = _get("/api/buckets")          # 小库直接全捞；大了再换向量检索
        name_terms = {t for t in str(meta.get("name") or "") if len(t.strip()) == 1}
        out = []
        for b in hits:
            if str(b.get("id")) == str(bucket.get("id")):
                continue
            blob = f"{b.get('name') or ''}{b.get('content_preview') or ''}"
            if name_terms and len(name_terms & set(blob)) >= max(2, len(name_terms) // 2):
                out.append({**b, "metadata": b})
        return out[:8]

    async def ask(prompt: str) -> str:
        resp = await llm.chat.completions.create(
            model=JUDGE_MODEL, max_tokens=300,
            messages=[{"role": "system", "content": cd.JUDGE_SYSTEM},
                      {"role": "user", "content": prompt}])
        return resp.choices[0].message.content or ""

    full = []
    for b in fresh:
        d = _detail(str(b.get("id") or ""))
        full.append(d or {**b, "metadata": b})

    hits = await cd.sweep(full, find_related, ask, max_retire=args.max)
    if not hits:
        print("[矛盾检测] 没发现被取代的旧记忆。")
        return 0

    print(f"[矛盾检测] 判定 {len(hits)} 条已被取代：")
    for v in hits:
        print(f"  · {v['old_name']}（{v['old_id']}）把握 {v['confidence']:.2f}")
        print(f"    {v['reason']}")

    if not args.write:
        print("\n[矛盾检测] 这是预演，什么都没动。确认没问题加 --write 再跑一次。")
        return 0

    done = []
    for v in hits:
        try:
            _post_trace(v["old_id"])
            done.append(v)
        except Exception as e:  # noqa: BLE001
            print(f"  沉底失败 {v['old_id']}：{e}")
    stale_ledger.record(done)
    print(f"\n[矛盾检测] 已沉底 {len(done)} 条。她可以在 Telegram 打 /stale 查看或撤销。")
    print("[矛盾检测] 沉底不是删除——桶还在，关键词照样捞得回来。")
    return 0


def _post_trace(bucket_id: str) -> None:
    body = json.dumps({"bucket_id": bucket_id, "resolved": 1}).encode()
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = "Bearer " + TOKEN
    req = urllib.request.Request(BRAIN + "/api/tools/trace", data=body,
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        resp.read()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
