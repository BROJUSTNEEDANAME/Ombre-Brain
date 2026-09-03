"""联网搜索的真跑测试。

存在的理由：写这段时 docs.z.ai 在我这边打不开，接口格式没法查证。所以代码
写成「两种调法都真打一次，哪种通就记住哪种」——那么这里必须验证「探测」这件事
本身是对的：通了要记住、没结果不算通、全挂了也不许炸。
"""
import asyncio
import sys
import types

import pytest

import web_search as ws


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    """替身 httpx：按 URL 决定给什么，并记下每次调用。"""

    def __init__(self, handler):
        self._handler = handler
        self.calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, headers=None, json=None):
        self.calls.append(url)
        return self._handler(url, json)


def _install(monkeypatch, handler):
    client = _Client(handler)
    monkeypatch.setattr(ws.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(ws, "_working", None)
    return client


def test_web_search_endpoint_results_are_parsed(monkeypatch):
    def handler(url, body):
        assert body["search_query"] == "明天上海天气"
        return _Resp({"search_result": [
            {"title": "上海天气", "link": "https://x/1", "content": "多云 18-24℃"}]})

    _install(monkeypatch, handler)
    hits = asyncio.run(ws.search("明天上海天气"))
    assert hits == [{"title": "上海天气", "link": "https://x/1", "body": "多云 18-24℃"}]


def test_it_falls_through_to_the_chat_flavor_and_remembers_it(monkeypatch):
    """第一种调法不通就试第二种；试出来之后不许每次都再白试一遍。"""
    def handler(url, body):
        if url.endswith("web_search"):
            return _Resp({"error": "unknown route"}, status=404)
        return _Resp({"choices": [{"message": {"tool_calls": [
            {"search_result": [{"title": "T", "url": "https://y/2", "content": "C"}]}]}}]})

    client = _install(monkeypatch, handler)
    assert asyncio.run(ws.search("票价"))[0]["link"] == "https://y/2"
    assert ws._working == "chat"
    n = len(client.calls)
    asyncio.run(ws.search("再搜一次"))
    assert len(client.calls) == n + 1, "已经知道走哪条了，不该再试错一次"


def test_http_200_with_no_results_is_not_treated_as_working(monkeypatch):
    """有的网关对不认识的 body 也回 200 配一个错误 JSON。只看状态码
    会把坏的那条记成好的，之后永远走在坏路上。"""
    def handler(url, body):
        if url.endswith("web_search"):
            return _Resp({"code": 400, "msg": "unsupported"})   # 200 但没结果
        return _Resp({"results": [{"name": "对的", "snippet": "内容"}]})

    _install(monkeypatch, handler)
    hits = asyncio.run(ws.search("什么"))
    assert hits and hits[0]["title"] == "对的"
    assert ws._working == "chat"


def test_search_never_raises_even_when_everything_is_down(monkeypatch):
    """搜索炸了不许打断她那一轮对话。"""
    def handler(url, body):
        raise RuntimeError("网络没了")

    _install(monkeypatch, handler)
    assert asyncio.run(ws.search("随便")) == []


def test_empty_query_costs_nothing(monkeypatch):
    client = _install(monkeypatch, lambda url, body: _Resp({}))
    assert asyncio.run(ws.search("   ")) == []
    assert client.calls == [], "空搜索词不该发请求"


def test_no_results_tells_him_to_say_so_instead_of_making_something_up():
    text = ws.format_for_model("明天天气", [])
    assert "没搜到" in text and "编" in text


def test_results_carry_their_source_links():
    text = ws.format_for_model("天气", [
        {"title": "上海天气", "link": "https://x/1", "body": "多云"}])
    assert "上海天气" in text and "https://x/1" in text and "多云" in text
