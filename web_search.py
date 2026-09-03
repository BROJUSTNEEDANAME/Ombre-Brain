"""联网搜索（z.ai 自带）。

⚠️ 为什么写成「自己去试」而不是写死一种调法：
写这段时 docs.z.ai 在我这边被网络策略挡着，打不开。凭印象把接口格式写死，
就是我刚跟她保证过不再干的事——说没验证过的话。所以这里放两种候选调法，
第一次真的各打一次，哪个通就记住哪个，之后只用那一个。

判定标准是「拿到了结果」，不是「HTTP 200」：某些网关对不认识的 body 也回 200
配一个错误 JSON，只看状态码会把坏的那条记成好的。
"""
from __future__ import annotations

import json
import logging
import os

import httpx

logger = logging.getLogger("ombre-search")

TIMEOUT = float(os.environ.get("OMBRE_SEARCH_TIMEOUT", "12"))
MAX_RESULTS = int(os.environ.get("OMBRE_SEARCH_MAX", "5"))

# 试出来的那种调法记在这儿，进程内只探一次
_working: str | None = None


def _base() -> str:
    return os.environ.get("LLM_BASE_URL", "https://api.z.ai/api/paas/v4/").strip().rstrip("/") + "/"


def _headers() -> dict:
    return {"Authorization": "Bearer " + (os.environ.get("LLM_API_KEY") or ""),
            "Content-Type": "application/json"}


def _pick(item: dict) -> dict | None:
    """从一条结果里挑出标题/链接/正文——各家字段名不一样，认全套别名。"""
    if not isinstance(item, dict):
        return None
    title = item.get("title") or item.get("name") or ""
    link = item.get("link") or item.get("url") or ""
    body = (item.get("content") or item.get("snippet")
            or item.get("summary") or item.get("description") or "")
    if not (title or body):
        return None
    return {"title": str(title)[:120], "link": str(link)[:300], "body": str(body)[:600]}


def _harvest(data) -> list[dict]:
    """在返回体里把结果数组捞出来。结构各家不同，宁可多找几个位置。"""
    out: list[dict] = []
    if isinstance(data, dict):
        for key in ("search_result", "search_results", "results", "data", "web_search"):
            value = data.get(key)
            if isinstance(value, list):
                out += [x for x in (_pick(i) for i in value) if x]
        # 有的把结果塞在 choices[0].message.tool_calls 里当文本回来
        for choice in (data.get("choices") or []):
            msg = (choice or {}).get("message") or {}
            for tc in (msg.get("tool_calls") or []):
                payload = ((tc or {}).get("search_result")
                           or (tc or {}).get("search_intent"))
                if isinstance(payload, list):
                    out += [x for x in (_pick(i) for i in payload) if x]
    elif isinstance(data, list):
        out += [x for x in (_pick(i) for i in data) if x]
    return out[:MAX_RESULTS]


async def _try(client: httpx.AsyncClient, flavor: str, query: str) -> list[dict]:
    if flavor == "web_search":
        r = await client.post(_base() + "web_search", headers=_headers(),
                              json={"search_engine": "search_std",
                                    "search_query": query})
    else:  # chat 端点上的搜索模型
        r = await client.post(_base() + "chat/completions", headers=_headers(),
                              json={"model": "web-search-pro",
                                    "messages": [{"role": "user", "content": query}],
                                    "stream": False})
    r.raise_for_status()
    return _harvest(r.json())


async def search(query: str) -> list[dict]:
    """搜一次。搜不到就返回空列表——绝不抛异常打断她那一轮对话。"""
    global _working
    query = (query or "").strip()
    if not query:
        return []
    order = [_working] if _working else ["web_search", "chat"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for flavor in order:
            try:
                hits = await _try(client, flavor, query)
            except Exception as e:  # noqa: BLE001
                logger.warning("搜索走 %s 失败：%s", flavor, str(e)[:160])
                continue
            if hits:
                if _working != flavor:
                    logger.info("联网搜索走通的是 %s", flavor)
                _working = flavor
                return hits
            logger.warning("搜索走 %s 通了但没有结果", flavor)
    return []


def format_for_model(query: str, hits: list[dict]) -> str:
    """喂回给模型的文本。带上链接，他才能把出处说给她听。"""
    if not hits:
        return f"「{query}」没搜到东西。跟她直说没查到，别自己编一个答案。"
    lines = [f"「{query}」的搜索结果（{len(hits)} 条，来自网络，不是你的记忆）："]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {h['title']}\n   {h['body']}"
                     + (f"\n   {h['link']}" if h["link"] else ""))
    return "\n".join(lines)
