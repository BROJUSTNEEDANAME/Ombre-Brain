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


def candidate_pairs(new_bucket: dict, others: Iterable[dict]) -> list[dict]:
    """挑出「值得拿去问模型」的旧记忆。

    排除：自己、已经沉底的、钉选/保护的、feel 域的。"""
    new_id = str(new_bucket.get("id") or "")
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
                max_retire: int = MAX_RETIRE_PER_SWEEP) -> list[dict]:
    """跑一轮。find_related(bucket)->候选列表；ask(prompt)->模型原文。

    I/O 全部通过这两个回调注入，所以这一整套逻辑可以脱网真跑测试。"""
    verdicts: list[dict] = []
    for new in new_buckets:
        try:
            others = await find_related(new)
        except Exception:  # noqa: BLE001
            continue                        # 捞不到候选就跳过这条，别让整轮挂掉
        for old in candidate_pairs(new, others or []):
            try:
                raw = await ask(judge_prompt(new, old))
            except Exception:  # noqa: BLE001
                continue
            v = parse_verdict(raw)
            v["old_id"] = str((old.get("metadata") or old).get("id") or old.get("id") or "")
            v["new_id"] = str(new.get("id") or (new.get("metadata") or {}).get("id") or "")
            v["old_name"] = str((old.get("metadata") or {}).get("name")
                                or old.get("name") or v["old_id"])
            verdicts.append(v)
    return decide(verdicts, min_confidence=min_confidence, max_retire=max_retire)


_WS = re.compile(r"\s+")


def short(text: str, n: int = 40) -> str:
    return _WS.sub(" ", str(text or "")).strip()[:n]
