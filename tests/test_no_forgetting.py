"""学 paramecium：搜到什么就是什么，没有遗忘曲线。

由来 2026-10-06：她在 Telegram 问「Eden」，他搜「Eden」「伊甸」「前夫」都说没找着。
库里其实有——「问答录#30 和前夫哥打起来谁赢」「怪物伪人模式（前夫的东西要丢吗）」——
但都被标成已解决、衰减进了 archive/，而关键词检索 include_archive=False，根本不看。
她：「记忆库的东西都没了吗？」
"""
import pytest


async def _archived(bucket_mgr, content, name, resolved=True):
    bid = await bucket_mgr.create(content=content, name=name, tags=["测试"], domain=["人际"])
    if resolved:
        await bucket_mgr.update(bid, resolved=True)   # 会被挪进 archive/
    else:
        await bucket_mgr.archive(bid)                 # 衰减引擎那条路
    return bid


@pytest.mark.asyncio
async def test_resolved_archived_bucket_is_found_by_keyword(bucket_mgr):
    bid = await _archived(bucket_mgr, "【闪闪问答录 #30 — 和前夫哥打起来谁赢】牌面显示两败俱伤。", "问答录30 和前夫哥打起来谁赢")
    found = await bucket_mgr.get(bid)
    assert found["metadata"].get("type") == "archived", "前提：它真的在归档里"
    hits = await bucket_mgr.search("前夫")
    assert bid in [h["id"] for h in hits], "归档里的前夫哥，关键词必须搜得到"


@pytest.mark.asyncio
async def test_decay_archived_bucket_is_found_by_keyword(bucket_mgr):
    bid = await _archived(bucket_mgr, "前夫的东西要丢吗——丢，不要他的东西。", "怪物伪人模式", resolved=False)
    hits = await bucket_mgr.search("前夫")
    assert bid in [h["id"] for h in hits]


@pytest.mark.asyncio
async def test_resolved_is_not_ranked_below_an_unrelated_live_bucket(bucket_mgr):
    """已解决不该被打到 0.3 倍——不然一样掉出前 N 名，等于没搜到。"""
    old = await _archived(bucket_mgr, "前夫哥 Eden 的事", "前夫哥 Eden")
    hits = await bucket_mgr.search("前夫哥 Eden")
    assert hits and hits[0]["id"] == old


@pytest.mark.asyncio
async def test_decay_cycle_no_longer_hides_memories(bucket_mgr, decay_eng):
    """没有遗忘曲线：分数再低，衰减周期也不许把桶挪进归档。"""
    bid = await bucket_mgr.create(content="很久以前的一件小事", name="小事", importance=1, arousal=0.0)
    decay_eng.threshold = 10_000          # 让任何分数都低于阈值
    stats = await decay_eng.run_decay_cycle()
    assert stats.get("archived", 0) == 0
    assert (await bucket_mgr.get(bid))["metadata"].get("type") != "archived"


@pytest.mark.asyncio
async def test_old_forgetting_behaviour_is_opt_in_only(test_config, bucket_mgr):
    from decay_engine import DecayEngine
    cfg = dict(test_config); cfg["decay"] = dict(test_config["decay"], auto_archive=True)
    eng = DecayEngine(cfg, bucket_mgr)
    bid = await bucket_mgr.create(content="很久以前的一件小事", name="小事", importance=1, arousal=0.0)
    eng.threshold = 10_000
    await eng.run_decay_cycle()
    assert (await bucket_mgr.get(bid))["metadata"].get("type") == "archived"
