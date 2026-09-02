# -*- coding: utf-8 -*-
"""矛盾检测：新记忆让旧记忆过时的时候，把旧的沉底。

由来：她的课表改了，但记忆库里还留着旧课表，他早上照着旧的念。
「他好不灵」有一部分不是笨，是**记着过期的事**。

设计上的四条红线（都在这个文件里用代码钉住，不靠调用方自觉）：

1. **只沉底，绝不删除。** 用 trace(resolved=1) 让它掉权重、沉到底，
   关键词还能捞回来。判错了代价是「他一时想不起」，不是「永远没了」。
2. **钉选/保护的桶一律不碰。** 那些是她亲手锁的核心准则。
3. **要有账、可撤销。** 每次沉底都记一笔，/stale 能列出来、能一键恢复。
4. **一轮最多沉底几条。** 模型抽风时的爆炸半径必须有上限——
   宁可漏判，也不能一次把记忆库扫掉一片。

判断本身交给模型（字符重叠做不到：「我女儿五岁」→「六岁」整句几乎一样，
只改一个字，按重叠算会被当成近重复而漏判——kiwi-mem 踩过这个坑）。
这个文件只负责：谁能进候选、怎么问、怎么读答案、最后允许动谁。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Iterable

# 一轮最多沉底多少条。模型抽风时的爆炸半径。
MAX_RETIRE_PER_SWEEP = 5
# 低于这个把握不动手
MIN_CONFIDENCE = 0.75
# 这些域不参与：feel 是他自己的感受，没有「过时」一说
SKIP_DOMAINS = {"feel", "沉淀物"}


def is_protected(meta: dict) -> bool:
    """钉选/保护的桶永远不动——那是她亲手锁的核心准则。"""
    return bool(meta.get("pinned") or meta.get("protected")
                or meta.get("type") == "permanent")


def _created(bucket: dict) -> str:
    meta = bucket.get("metadata") or bucket
    return str(meta.get("created") or "")


def candidate_pairs(new_bucket: dict, others: Iterable[dict]) -> list[dict]:
    """挑出「值得拿去问模型」的旧记忆。

    排除：自己、已经沉底的、钉选/保护的、feel 域的、**以及比它新的**。

    ⚠️ 最后那条是方向问题，真跑才发现的：两条都在最近几天里时，它们会各自
    轮流当「新的」，于是新旧互相判成被取代——**当前有效的那条也会被沉底**。
    只有比它老的才有资格被它取代。取不到时间的一律不碰。"""
    new_id = str(new_bucket.get("id") or "")
    new_at = _created(new_bucket)
    out = []
    for old in others:
        meta = old.get("metadata") or old
        old_id = str(old.get("id") or meta.get("id") or "")
        if not old_id or old_id == new_id:
            continue
        if meta.get("resolved"):
            continue                       # 已经沉底了，不用再沉
        if is_protected(meta):
            continue
        domains = meta.get("domain") or []
        if isinstance(domains, str):
            domains = [domains]
        if SKIP_DOMAINS & {str(d) for d in domains}:
            continue
        old_at = _created(old)
        if not new_at or not old_at or old_at >= new_at:
            continue                       # 只有更老的才可能被取代
        out.append(old)
    return out


JUDGE_SYSTEM = (
    "你在维护一个人的长期记忆库。给你两条记忆：一条新的、一条旧的。\n"
    "只判断一件事：**新的这条是否让旧的那条不再成立**。\n\n"
    "算「过时」的：同一件事实被改写了——换了工作、搬了家、改了课表、"
    "分了手、换了偏好、数字变了（五岁→六岁）、计划取消了。\n"
    "不算「过时」的：\n"
    "  · 新的只是补充细节、换个说法、或者讲同一件事的另一面\n"
    "  · 两条可以同时成立（喜欢猫 / 也喜欢狗）\n"
    "  · 讲的是不同的人、不同的事、不同的时间点\n"
    "  · 只是情绪或感受的记录\n\n"
    "拿不准就判否。漏判只是他一时想不起；误判会让真实的记忆沉底。\n"
    '只输出 JSON：{"superseded": true/false, "confidence": 0~1, "reason": "一句话"}'
)


def judge_prompt(new_bucket: dict, old_bucket: dict, limit: int = 1200) -> str:
    def _text(b):
        meta = b.get("metadata") or {}
        name = meta.get("name") or b.get("name") or ""
        created = meta.get("created") or b.get("created") or ""
        body = b.get("content") or b.get("content_preview") or ""
        return f"标题：{name}\n时间：{created}\n内容：{str(body)[:limit]}"

    return (f"【新记忆】\n{_text(new_bucket)}\n\n"
            f"【旧记忆】\n{_text(old_bucket)}\n\n"
            "旧的这条被新的取代了吗？")


def parse_verdict(raw: str) -> dict:
    """从模型返回里抠出裁决。抠不出来一律当「不动」。"""
    text = str(raw or "")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return {"superseded": False, "confidence": 0.0, "reason": "没读到 JSON"}
    try:
        data = json.loads(text[start:end + 1])
    except Exception:  # noqa: BLE001
        return {"superseded": False, "confidence": 0.0, "reason": "JSON 解析失败"}
    if not isinstance(data, dict):
        return {"superseded": False, "confidence": 0.0, "reason": "不是对象"}
    try:
        conf = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "superseded": data.get("superseded") is True,
        "confidence": max(0.0, min(1.0, conf)),
        "reason": str(data.get("reason") or "")[:200],
    }


def decide(verdicts: list[dict], *, min_confidence: float = MIN_CONFIDENCE,
           max_retire: int = MAX_RETIRE_PER_SWEEP) -> list[dict]:
    """哪些真的动手。把握高的排前面，砍到上限为止。

    每项形如 {"old_id":..., "new_id":..., "superseded":bool,
              "confidence":float, "reason":str}
    """
    keep = [v for v in verdicts
            if v.get("superseded") and float(v.get("confidence") or 0) >= min_confidence
            and v.get("old_id")]
    keep.sort(key=lambda v: float(v.get("confidence") or 0), reverse=True)
    seen, out = set(), []
    for v in keep:
        if v["old_id"] in seen:
            continue
        seen.add(v["old_id"])
        out.append(v)
        if len(out) >= max_retire:
            break
    return out


async def sweep(new_buckets: list[dict], find_related, ask, *,
                min_confidence: float = MIN_CONFIDENCE,
                max_retire: int = MAX_RETIRE_PER_SWEEP,
                on_progress=None, on_verdict=None,
                concurrency: int = 4) -> list[dict]:
    """跑一轮。find_related(bucket)->候选列表；ask(prompt)->模型原文。

    I/O 全部通过这两个回调注入，所以这一整套逻辑可以脱网真跑测试。

    on_verdict(v) 每得到一条裁决叫一次——判官说了什么必须能看见，
    否则「没发现」到底是真没有、还是判官太保守、还是配对配错了，分不出来。
    on_progress(i, total, pairs_done) 每处理完一条新记忆叫一次——不给进度的话
    她只能看着光标不动，以为卡死了（真的发生过）。
    concurrency 控制同时问模型几条：串行 150 次要十几分钟。"""
    verdicts: list[dict] = []
    sem = asyncio.Semaphore(max(1, concurrency))
    total = len(new_buckets)

    async def judge(new: dict, old: dict) -> dict | None:
        async with sem:
            try:
                raw = await ask(judge_prompt(new, old))
            except Exception:  # noqa: BLE001
                return None
        v = parse_verdict(raw)
        v["old_id"] = str((old.get("metadata") or old).get("id") or old.get("id") or "")
        v["new_id"] = str(new.get("id") or (new.get("metadata") or {}).get("id") or "")
        v["old_name"] = str((old.get("metadata") or {}).get("name")
                            or old.get("name") or v["old_id"])
        v["new_name"] = str((new.get("metadata") or {}).get("name")
                            or new.get("name") or v["new_id"])
        return v

    for i, new in enumerate(new_buckets, 1):
        try:
            others = await find_related(new)
        except Exception:  # noqa: BLE001
            others = []                     # 捞不到候选就跳过这条，别让整轮挂掉
        pairs = candidate_pairs(new, others or [])
        if pairs:
            got = await asyncio.gather(*(judge(new, old) for old in pairs))
            verdicts.extend(v for v in got if v)
            if on_verdict:
                for v in got:
                    if v:
                        on_verdict(v)
        if on_progress:
            on_progress(i, total, len(verdicts))
    return decide(verdicts, min_confidence=min_confidence, max_retire=max_retire)


_WS = re.compile(r"\s+")


def short(text: str, n: int = 40) -> str:
    return _WS.sub(" ", str(text or "")).strip()[:n]
