"""矛盾检测的真跑测试。

这套东西动的是她的记忆库——判错了他就想不起真事。所以四条红线每条都要有测试：
只沉底不删除、钉选不碰、拿不准不动、一轮有上限。
"""
import asyncio
import json

import contradiction as cd


OLD = "2020-01-01T00:00:00+00:00"      # 默认当「旧记忆」
NEW = "2026-01-01T00:00:00+00:00"      # 显式传这个当「新记忆」


def _b(bid, name="", content="", created=OLD, **meta):
    return {"id": bid, "content": content,
            "metadata": {"id": bid, "name": name, "created": created, **meta}}


# ---------------------------------------------------------------- 候选过滤

def test_pinned_and_protected_are_never_candidates():
    """她亲手锁的核心准则，一条都不许进候选。"""
    new = _b("new1", "新课表", created=NEW)
    others = [
        _b("p1", "核心准则", pinned=True),
        _b("p2", "红线", protected=True),
        _b("p3", "永久", type="permanent"),
        _b("ok", "旧课表"),
    ]
    assert [b["id"] for b in cd.candidate_pairs(new, others)] == ["ok"]


def test_feel_and_already_resolved_are_skipped():
    """feel 是他自己的感受，没有「过时」一说；已经沉底的不用再沉。"""
    new = _b("new1", created=NEW)
    others = [
        _b("f1", domain=["feel"]),
        _b("f2", domain="沉淀物"),
        _b("r1", resolved=True),
        _b("ok"),
        _b("new1", created=NEW),         # 自己
    ]
    assert [b["id"] for b in cd.candidate_pairs(new, others)] == ["ok"]


# ---------------------------------------------------------------- 读裁决

def test_parse_verdict_reads_json_out_of_chatty_output():
    v = cd.parse_verdict('好的，我的判断是：\n{"superseded": true, '
                         '"confidence": 0.9, "reason": "课表改了"}\n希望有帮助')
    assert v == {"superseded": True, "confidence": 0.9, "reason": "课表改了"}


def test_unparseable_output_never_retires_anything():
    """读不懂就当「不动」。宁可漏判，不能瞎沉底。"""
    junk_inputs = (
        "",                                  # 空
        "不知道",                             # 没有花括号
        "{坏的 json",                         # 有开括号没闭括号
        '{"superseded": true, 少了引号}',      # 有完整括号但 JSON 语法坏 ← 会走解析失败分支
        '{"superseded": true,,}',            # 同上
        "null", "[1,2]",                     # 解析得出来但不是对象
        '{"superseded": "yes"}',             # 是对象但不是布尔真
    )
    for junk in junk_inputs:
        v = cd.parse_verdict(junk)
        assert v["superseded"] is False, junk


def test_confidence_is_clamped():
    assert cd.parse_verdict('{"superseded":true,"confidence":9}')["confidence"] == 1.0
    assert cd.parse_verdict('{"superseded":true,"confidence":-3}')["confidence"] == 0.0
    assert cd.parse_verdict('{"superseded":true,"confidence":"高"}')["confidence"] == 0.0


# ---------------------------------------------------------------- 动手范围

def test_low_confidence_is_not_acted_on():
    out = cd.decide([{"old_id": "a", "superseded": True, "confidence": 0.5}])
    assert out == []


def test_blast_radius_is_capped():
    """模型抽风时不能一次把记忆库扫掉一片。"""
    many = [{"old_id": f"b{i}", "superseded": True, "confidence": 0.99}
            for i in range(50)]
    assert len(cd.decide(many)) == cd.MAX_RETIRE_PER_SWEEP


def test_highest_confidence_wins_and_no_duplicates():
    out = cd.decide([
        {"old_id": "a", "superseded": True, "confidence": 0.80},
        {"old_id": "b", "superseded": True, "confidence": 0.95},
        {"old_id": "a", "superseded": True, "confidence": 0.99},
    ], max_retire=2)
    assert [v["old_id"] for v in out] == ["a", "b"]


# ---------------------------------------------------------------- 整轮真跑

def _run(coro):
    return asyncio.run(coro)


def test_sweep_end_to_end_retires_the_stale_schedule():
    """真实场景：课表改了，旧课表该沉底，无关的记忆不许被碰。"""
    new = _b("new1", "新课表", "这学期周二周四上课", created=NEW)
    old_schedule = _b("old1", "旧课表", "这学期周一周三上课")
    unrelated = _b("old2", "她怕打雷", "她说打雷睡不着")

    async def find_related(_b_):
        return [old_schedule, unrelated]

    async def ask(prompt):
        if "旧课表" in prompt:
            return '{"superseded": true, "confidence": 0.93, "reason": "课表被改写"}'
        return '{"superseded": false, "confidence": 0.9, "reason": "两件事"}'

    out = _run(cd.sweep([new], find_related, ask))
    assert [v["old_id"] for v in out] == ["old1"]
    assert out[0]["new_id"] == "new1"
    assert "课表" in out[0]["reason"]


def test_sweep_survives_a_broken_judge():
    """模型报错/超时不许把整轮拖垮，也不许因此误伤。"""
    async def find_related(_b_):
        return [_b("old1", "旧的")]

    async def ask(_p):
        raise RuntimeError("模型超时")

    assert _run(cd.sweep([_b("new1", created=NEW)], find_related, ask)) == []


def test_sweep_survives_a_broken_search():
    async def find_related(_b_):
        raise RuntimeError("大脑没响应")

    async def ask(_p):
        raise AssertionError("捞不到候选就不该问模型")

    assert _run(cd.sweep([_b("new1", created=NEW)], find_related, ask)) == []


def test_judge_prompt_tells_the_model_to_abstain_when_unsure():
    """提示词里必须明说「拿不准判否」——这是漏判优先于误判的来源。"""
    assert "拿不准就判否" in cd.JUDGE_SYSTEM
    p = cd.judge_prompt(_b("n", "新", "内容A"), _b("o", "旧", "内容B"))
    assert "内容A" in p and "内容B" in p and "新" in p and "旧" in p


def test_only_older_memories_can_be_superseded():
    """方向必须钉死：只有比新记忆更老的才有资格被取代。

    真跑时发现的漏洞：两条都在最近几天里，它们会各自轮流当「新的」，
    于是新旧互相判成被取代——**当前有效的那条也会被沉底**。"""
    new = _b("new1", "新课表", created="2026-09-02T00:00:00+00:00")
    others = [
        _b("older", "旧课表", created="2026-09-01T00:00:00+00:00"),
        _b("newer", "更新的课表", created="2026-09-03T00:00:00+00:00"),
        _b("same", "同时", created="2026-09-02T00:00:00+00:00"),
        _b("notime", "没时间戳", created=""),
    ]
    assert [b["id"] for b in cd.candidate_pairs(new, others)] == ["older"]


def test_new_bucket_without_timestamp_touches_nothing():
    """新记忆自己没有时间戳时，无法判断方向——一条都不碰。"""
    new = _b("new1", "新课表", created="")
    assert cd.candidate_pairs(new, [_b("o", created=OLD)]) == []
