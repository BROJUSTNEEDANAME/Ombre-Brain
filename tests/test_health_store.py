"""闪闪的身体数据落盘 + 读回。

思路学自 KKarsyline/Collar_watch，代码全部另写（那个仓库 PolyForm 非商业许可）。
这个文件盯三件最容易出事的地方：
1. 太旧的数据绝不能当成「此刻」注入给他（CLAUDE.md 第五条：假的现在比没有更坏）。
2. 只收 allow-list 里的指标，脏数据丢掉。
3. 只碰 health 目录，任何情况都不碰记忆桶。
"""
import json
import os
import time

import pytest


@pytest.fixture
def hs(tmp_path, monkeypatch):
    monkeypatch.setenv("OMBRE_HEALTH_DIR", str(tmp_path))
    import importlib
    import health_store
    importlib.reload(health_store)
    return health_store


def test_ingest_accepts_known_metrics_and_drops_junk(hs):
    r = hs.ingest({"heart_rate": 82, "hrv": 45, "garbage": 1, "___": "x"})
    assert r["accepted"] == 2
    assert "心率 82bpm" in hs.snapshot()


def test_ingest_reads_the_metrics_array_shape(hs):
    """HAE 那种 {"metrics":[...]} 也得认。"""
    r = hs.ingest({"metrics": [
        {"name": "resting_heart_rate", "value": 68, "ts": time.time()},
        {"name": "sleep_hours", "qty": 6.5, "date": time.time()},
    ]})
    assert r["accepted"] == 2
    snap = hs.snapshot()
    assert "静息心率 68" in snap and "睡眠 6.5" in snap


def test_stale_data_is_never_presented_as_now(hs):
    """核心那条。整份数据都超过 max_age_h，就返回空——绝不注入。"""
    hs.ingest({"heart_rate": 90})
    # 把 latest 的时间戳改老
    p = os.path.join(os.environ["OMBRE_HEALTH_DIR"], "latest.json")
    d = json.load(open(p, encoding="utf-8"))
    for v in d.values():
        v["ts"] = time.time() - 10 * 3600
    json.dump(d, open(p, "w", encoding="utf-8"))
    assert hs.snapshot(max_age_h=6) == "", "十小时前的心率不该被当成此刻"
    # 但放宽窗口能查到
    assert "心率 90" in hs.snapshot(max_age_h=24)


def test_a_stale_single_metric_is_dropped_from_a_fresh_snapshot(hs):
    """一份快照里，新的心率能进，但那条几小时前的旧睡眠不该混进来当『此刻』。"""
    now = time.time()
    hs.ingest({"metrics": [
        {"name": "heart_rate", "value": 80, "ts": now},
        {"name": "step_count", "value": 3000, "ts": now - 9 * 3600},
    ]})
    snap = hs.snapshot(max_age_h=6)
    assert "心率 80" in snap
    assert "步数" not in snap, "9 小时前的步数不该出现在此刻快照里"


def test_snapshot_always_labels_how_old(hs):
    """每一项都必须带『多久之前』——这是他判断能不能信这个数的依据。"""
    hs.ingest({"heart_rate": 75})
    snap = hs.snapshot()
    assert "（" in snap and "）" in snap
    assert "前" in snap or "刚刚" in snap


def test_out_of_order_upload_never_overwrites_newer_with_older(hs):
    """乱序上报：先来一个新的，再来一个更旧的，latest 得保留新的。"""
    now = time.time()
    hs.ingest({"metrics": [{"name": "heart_rate", "value": 88, "ts": now}]})
    hs.ingest({"metrics": [{"name": "heart_rate", "value": 60, "ts": now - 3600}]})
    assert "心率 88" in hs.snapshot()


def test_no_data_returns_empty_not_a_fake_zero(hs):
    assert hs.snapshot() == ""
    assert hs.ingest({"unknown": 1})["accepted"] == 0


def test_it_only_touches_the_health_dir(hs):
    """写坏了最多丢心率，绝不碰记忆桶。"""
    import inspect
    src = inspect.getsource(hs)
    assert "buckets" not in src.lower(), "健康模块不该有任何 buckets 字样"


def test_detail_drills_into_recent_points(hs):
    now = time.time()
    for hr in (70, 80, 90):
        hs.ingest({"metrics": [{"name": "heart_rate", "value": hr, "ts": now}]})
    d = hs.detail("hr", hours=24)
    assert "最低 70" in d and "最高 90" in d and "3 个点" in d


def test_the_ingest_endpoint_requires_a_token(hs):
    """server.py 要整个 MCP 框架才能加载，单测跑不起它——所以这条查源码：
    ingest 路由必须校验 token，且没设 token 时整个关掉（不许裸奔：
    谁都能 POST = 谁都能往她健康记录里塞假数据）。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "server.py").read_text(encoding="utf-8")
    i = src.index('async def health_ingest')
    body = src[i:i + 1400]
    assert "OMBRE_HEALTH_TOKEN" in body
    assert "hmac.compare_digest" in body, "token 比对要用 compare_digest 防时序攻击"
    assert "status_code=403" in body, "token 不对要拒"
    assert "status_code=404" in body, "没设 token 要整个关掉，不能裸奔"
    # 只写 health，绝不碰记忆桶
    assert "bucket" not in body.lower()


def test_the_ingest_endpoint_only_calls_the_health_store(hs):
    """路由体里只能调 health_store.ingest，不许顺手写别的。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "server.py").read_text(encoding="utf-8")
    i = src.index('async def health_ingest')
    body = src[i:i + 1400]
    assert "health_store.ingest(payload)" in body
