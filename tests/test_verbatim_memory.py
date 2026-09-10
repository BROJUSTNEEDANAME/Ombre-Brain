"""原文是唯一真相（学 paramecium）。

她的原话：「检索居然是概率的？我认为检索绝对不应该是概率的。」
  1. query 逐字出现在正文里的桶，必然捞回来、排最前——不看权重、不看衰减。
  2. 钉选桶 = 真相本身。breath 给他的必须是原文，不许脱水改写。
逻辑在 verbatim_recall.py（零依赖），这里真的跑它；再锚定断言 server.breath 确实调了它。
"""
import re
from pathlib import Path

from verbatim_recall import merge_verbatim, wants_verbatim

_SERVER = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")


def _b(bid, content, **meta):
    return {"id": bid, "content": content, "metadata": {"pinned": False, **meta}}


def test_verbatim_hit_is_found_even_when_ranking_misses_it():
    a = _b("a", "她的男友就是 Nikto 自己，不是别人。")
    b = _b("b", "今天吃了粥。")
    out = merge_verbatim("男友", matches=[], all_buckets=[a, b])
    assert [x["id"] for x in out] == ["a"]
    assert a["verbatim_hit"] is True and "verbatim_hit" not in b


def test_verbatim_hits_go_first_and_ranked_ones_get_marked_too():
    ranked = _b("r", "模糊排名捞到的，正文里也有 男友 两个字")
    other = _b("o", "无关")
    missed = _b("m", "排名漏掉的：她男友是谁——就是他。")
    out = merge_verbatim("男友", matches=[ranked, other], all_buckets=[ranked, other, missed])
    assert [x["id"] for x in out] == ["m", "r", "o"], "漏掉的排最前，原排名顺序不动"
    assert ranked["verbatim_hit"] is True
    assert "verbatim_hit" not in other


def test_verbatim_match_is_case_insensitive_and_needs_two_chars():
    a = _b("a", "她说 NIKTO 是她男友")
    assert [x["id"] for x in merge_verbatim("nikto", [], [a])] == ["a"]
    assert merge_verbatim("一", [], [_b("x", "一个字太滥")]) == []
    assert merge_verbatim("  ", [], [a]) == []


def test_pinned_and_verbatim_hits_want_the_original_text():
    assert wants_verbatim(_b("p", "x", pinned=True))
    assert wants_verbatim(_b("q", "x", protected=True))
    hit = _b("h", "x"); hit["verbatim_hit"] = True
    assert wants_verbatim(hit)
    assert not wants_verbatim(_b("n", "x")), "普通桶照旧脱水——省 token 的机制不动"


def _breath_body() -> str:
    i = _SERVER.index("async def breath(")
    j = _SERVER.index("@mcp.tool()", i)
    return _SERVER[i:j]


def test_server_breath_uses_the_verbatim_channel():
    body = _breath_body()
    assert "merge_verbatim(query, matches," in body
    assert "if wants_verbatim(bucket):" in body
    # 命中的桶要有标记，让他知道这是原话不是联想
    assert "[逐字命中]" in body


def test_server_surfacing_gives_pinned_verbatim_not_dehydrated():
    body = _breath_body()
    i = body.index("pinned_results = []")
    j = body.index("pinned_results.append(")
    seg = body[i:j]
    assert 'strip_wikilinks(b["content"]).strip()' in seg
    assert "dehydrator.dehydrate" not in seg, "钉选是真相，不许脱水"
