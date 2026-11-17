"""breath(domain="feel") 必须真的读到 feel。

真事：feel 分支写在「无 query → 浮现」分支后面，而 breath(domain="feel") 本来就不带
query，于是永远走不到——读回来的是一整屏钉选桶，一条 feel 都没有。
server.py 依赖重、测试环境里 import 不了，所以这里把 breath 的函数体从源码里抠出来，
配上替身真的执行一遍（不 skip）。
"""
import ast
import asyncio
import logging
import random
from pathlib import Path

_SRC = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")


def _load_breath(buckets):
    tree = ast.parse(_SRC)
    fn = next(n for n in tree.body
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "breath")
    fn.decorator_list = []
    mod = ast.Module(body=[fn], type_ignores=[])

    class _BM:
        async def list_all(self, include_archive=False):
            return buckets

    class _Decay:
        async def ensure_started(self):
            pass

        def calculate_score(self, meta):
            return 1.0

    class _Dehy:
        async def dehydrate(self, content, meta):
            return content

    ns = {
        "bucket_mgr": _BM(), "decay_engine": _Decay(), "dehydrator": _Dehy(),
        "count_tokens_approx": len, "strip_wikilinks": lambda s: s,
        "logger": logging.getLogger("t"), "random": random,
    }
    exec(compile(mod, "server.py", "exec"), ns)
    return ns["breath"]


def _b(bid, content, **meta):
    return {"id": bid, "content": content, "metadata": {"created": "2026-01-01", **meta}}


_BUCKETS = [
    _b("pin1", "钉选正文甲", pinned=True),
    _b("feel1", "我带走的那句话", type="feel"),
    _b("dyn1", "一件没解决的事"),
]


def test_feel_domain_without_query_returns_feels_not_pinned():
    out = asyncio.run(_load_breath(_BUCKETS)(domain="feel"))
    assert "我带走的那句话" in out, out
    assert "钉选正文甲" not in out, "feel 通道不该吐钉选桶"
    assert "一件没解决的事" not in out


def test_feel_domain_says_so_when_there_are_none():
    out = asyncio.run(_load_breath([_BUCKETS[0], _BUCKETS[2]])(domain="feel"))
    assert out == "没有留下过 feel。", out


def test_plain_breath_still_surfaces_pinned_and_unresolved_without_feels():
    out = asyncio.run(_load_breath(_BUCKETS)())
    assert "钉选正文甲" in out and "一件没解决的事" in out, out
    assert "我带走的那句话" not in out, "feel 不参与普通浮现"
