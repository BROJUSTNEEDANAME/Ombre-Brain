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
import env_file                       # noqa: E402
import stale_ledger                   # noqa: E402

# ⚠️ 必须在读下面这些环境变量之前加载。key 都在 systemd 的 EnvironmentFile 里，
# shell 里没有——手动跑 backfill 和这个脚本各踩过一次「missing API key」。
_ENV_USED = env_file.load()

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
    """取单条的完整正文。拿回来不是字典就当没拿到——别让脏数据一路往下走，
    最后表现成「跑完了、0 次判断」这种看起来正常的静默失败。"""
    try:
        got = _get("/api/bucket/" + urllib.parse.quote(bucket_id))
    except Exception:  # noqa: BLE001
        return {}
    return got if isinstance(got, dict) else {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=2, help="只看最近几天的新记忆")
    ap.add_argument("--write", action="store_true", help="真的沉底（默认只预演）")
    ap.add_argument("--max", type=int, default=cd.MAX_RETIRE_PER_SWEEP,
                    help="最多沉底几条")
    ap.add_argument("--limit", type=int, default=40,
                    help="这轮最多检查几条新记忆（从最新的开始）")
    ap.add_argument("--max-pairs", type=int, default=150,
                    help="最多问模型几次。默认值按「一次几分钱」定的，别去掉")
    args = ap.parse_args()

    if _ENV_USED:
        print(f"[矛盾检测] 配置读自 {_ENV_USED}")
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("ZAI_API_KEY")):
        print("[矛盾检测] 没读到 LLM_API_KEY —— 判官模型没法调。")
        print("  它应该在 .env.apibot 里；确认那个文件当前用户读得到。")
        return 1

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
    fresh.sort(key=lambda b: str((b if "created" in b else b.get("metadata") or {})
                                 .get("created") or ""), reverse=True)
    if len(fresh) > args.limit:
        print(f"[矛盾检测] 只看最新的 {args.limit} 条（--limit 可调）")
        fresh = fresh[:args.limit]

    all_buckets = _get("/api/buckets")          # 只取一次，别每条都重拉一遍全库
    print(f"[矛盾检测] 库里一共 {len(all_buckets)} 条，开始配对")

    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
    except ModuleNotFoundError:
        print("[矛盾检测] 没装 openai 包。用仓库自己的虚拟环境跑：")
        print("  .venv/bin/python sweep_contradictions.py ...")
        return 1
    llm = AsyncOpenAI(
        api_key=(os.environ.get("LLM_API_KEY") or os.environ.get("ZAI_API_KEY", "")).strip(),
        base_url=os.environ.get("LLM_BASE_URL", "https://api.z.ai/api/paas/v4/"),
        timeout=60.0, max_retries=0)

    async def find_related(bucket: dict) -> list[dict]:
        """粗筛出可能讲同一件事的旧记忆，交给模型细判。

        这里只做便宜的字符重合初筛——目的是把上千次模型调用砍到几十次。
        真正的判断在模型那边（字符比对判不了「五岁→六岁」）。"""
        meta = bucket.get("metadata") or bucket
        terms = {c for c in str(meta.get("name") or "") if c.strip()}
        if len(terms) < 2:
            return []
        need = max(2, len(terms) // 2)
        scored = []
        for b in all_buckets:
            if str(b.get("id")) == str(bucket.get("id")):
                continue
            blob = f"{b.get('name') or ''}{b.get('content_preview') or ''}"
            hit = len(terms & set(blob))
            if hit >= need:
                scored.append((hit, {**b, "metadata": b}))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [b for _s, b in scored[:5]]

    asked = {"n": 0}

    async def ask(prompt: str) -> str:
        if asked["n"] >= args.max_pairs:
            raise RuntimeError("到达 --max-pairs 上限")
        asked["n"] += 1
        resp = await llm.chat.completions.create(
            model=JUDGE_MODEL, max_tokens=300,
            messages=[{"role": "system", "content": cd.JUDGE_SYSTEM},
                      {"role": "user", "content": prompt}])
        return resp.choices[0].message.content or ""

    full, thin = [], 0
    for b in fresh:
        d = _detail(str(b.get("id") or ""))
        if not d:
            thin += 1
        full.append(d or {**b, "metadata": b})
    if thin:
        print(f"[矛盾检测] 其中 {thin} 条取不到完整正文，只按摘要判（可能偏保守）")

    hits = await cd.sweep(full, find_related, ask, max_retire=args.max)
    print(f"[矛盾检测] 问了模型 {asked['n']} 次"
          + ("（到上限了，--max-pairs 可调）" if asked["n"] >= args.max_pairs else ""))
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
