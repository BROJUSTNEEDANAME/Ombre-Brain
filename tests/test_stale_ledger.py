"""账本的真跑测试。

自动沉底如果没有账、不能撤销，就是一个会悄悄吞记忆的黑箱。
所以「记得下、看得见、撤得掉」三件事都要有测试。
"""
import json
from pathlib import Path

import stale_ledger as sl


def _p(tmp_path):
    return tmp_path / "stale_ledger.json"


def test_record_then_pending_shows_it(tmp_path):
    p = _p(tmp_path)
    sl.record([{"old_id": "a1", "old_name": "旧课表", "new_id": "b2",
                "reason": "课表改了", "confidence": 0.93}], p)
    items = sl.pending(p)
    assert len(items) == 1
    assert items[0]["old_id"] == "a1" and items[0]["undone"] is False
    assert items[0]["confidence"] == 0.93


def test_undo_hides_it_but_keeps_the_record(tmp_path):
    """撤销是标记，不是抹掉——她得能看见这件事发生过。"""
    p = _p(tmp_path)
    sl.record([{"old_id": "a1", "old_name": "旧课表"}], p)
    assert sl.mark_undone("a1", p) is True
    assert sl.pending(p) == []
    assert len(sl.load(p)) == 1 and sl.load(p)[0]["undone"] is True
    assert sl.mark_undone("a1", p) is False        # 已经撤过了


def test_no_body_text_is_ever_stored(tmp_path):
    """只记元信息。账本不该变成记忆正文的第二份拷贝。"""
    p = _p(tmp_path)
    sl.record([{"old_id": "a1", "old_name": "旧课表", "reason": "改了",
                "content": "这里是记忆正文，绝不该被写进账本"}], p)
    assert "记忆正文" not in p.read_text(encoding="utf-8")
    assert set(sl.load(p)[0]) == {"old_id", "old_name", "new_id", "reason",
                                  "confidence", "at", "undone"}


def test_entries_without_id_are_dropped(tmp_path):
    p = _p(tmp_path)
    sl.record([{"old_name": "没有ID"}, {"old_id": "ok"}], p)
    assert [e["old_id"] for e in sl.load(p)] == ["ok"]


def test_ledger_is_capped_and_newest_first(tmp_path):
    p = _p(tmp_path)
    for i in range(sl.MAX_ENTRIES + 20):
        sl.record([{"old_id": f"b{i}"}], p)
    data = sl.load(p)
    assert len(data) == sl.MAX_ENTRIES
    assert data[0]["old_id"] == f"b{sl.MAX_ENTRIES + 19}"      # 最新的在最前


def test_corrupt_ledger_never_crashes(tmp_path):
    """账本坏了不许把 /stale 弄崩——最多是这次看不到历史。"""
    p = _p(tmp_path)
    p.write_text("{坏的", encoding="utf-8")
    assert sl.load(p) == [] and sl.pending(p) == []
    sl.record([{"old_id": "a1"}], p)
    assert [e["old_id"] for e in sl.load(p)] == ["a1"]
