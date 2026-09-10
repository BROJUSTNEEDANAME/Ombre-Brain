"""原文优先的两条保证（学 paramecium：「原文是唯一真相，后面一切只是给它做索引」）。

她的原话：「检索居然是概率的？我认为检索绝对不应该是概率的。」
bucket_manager.search 是模糊打分 + 时间衰减 + 情绪共振的**排名**——一条旧事实会随时间
从前 N 名里掉出去；breath 再把结果交给脱水器（模型重写），每次措辞都会漂。
这两条把「还原」这件事从概率里捞出来：

  1. merge_verbatim：query 逐字出现在正文里的桶，**必然**捞回来、排最前，不看权重。
     这是 FTS5 逐字检索的等价物，纯字符串匹配，确定性。
  2. wants_verbatim：钉选/受保护桶、以及逐字命中的桶，给**原文**，不脱水。
     摘要是索引，不是替代品；对真相连索引都不该顶替原文。

零依赖：server.py 在测试沙箱里 import 不动（缺 mcp/openai/chromadb），
而仓库规矩是缺依赖不许 skip——所以逻辑放这儿，让测试真的跑到它。
"""
from __future__ import annotations

MIN_QUERY_LEN = 2   # 单字命中太滥（"一""的"什么都中），两个字起


def merge_verbatim(query: str, matches: list[dict], all_buckets: list[dict]) -> list[dict]:
    """把逐字命中的桶合进 matches：命中的标 verbatim_hit=True，没在 matches 里的排到最前。

    不改传入的 matches 顺序（排名那层的判断照旧），只在前面插入它漏掉的逐字命中。
    """
    q = (query or "").strip().lower()
    if len(q) < MIN_QUERY_LEN:
        return list(matches)
    seen = {b.get("id") for b in matches}
    hits = []
    for b in all_buckets:
        if q in (b.get("content") or "").lower() and b.get("id") not in seen:
            b["verbatim_hit"] = True
            hits.append(b)
    for b in matches:
        if q in (b.get("content") or "").lower():
            b["verbatim_hit"] = True
    return hits + list(matches)


def wants_verbatim(bucket: dict) -> bool:
    """这个桶该给原文而不是脱水摘要吗：钉选/受保护，或逐字命中。"""
    meta = bucket.get("metadata") or {}
    return bool(meta.get("pinned") or meta.get("protected") or bucket.get("verbatim_hit"))
