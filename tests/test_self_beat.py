"""他自己定下次什么时候找她：排程纯函数。"""
from datetime import datetime

import self_beat as B


def T(s):
    return datetime.fromisoformat(s)


def test_tag_on_its_own_line_is_removed_and_read():
    assert B.pop_tag("在。\n过来。\n[心跳:20]") == ("在。\n过来。", 20)
    assert B.pop_tag("嗯。\n[心跳：3 分钟]") == ("嗯。", 3)
    assert B.pop_tag("嗯。\n[heartbeat: 40 min]") == ("嗯。", 40)
    # 写了两次，以最后一次为准，两行都删
    assert B.pop_tag("[心跳:10]\n嗯。\n[心跳:30]") == ("嗯。", 30)


def test_tag_quoted_inside_a_sentence_is_left_alone():
    assert B.pop_tag("她说 [心跳:5] 是什么意思。") == ("她说 [心跳:5] 是什么意思。", None)
    assert B.pop_tag("嗯。") == ("嗯。", None)
    assert B.pop_tag("") == ("", None)
    # 超过 4 位不认
    assert B.pop_tag("嗯。\n[心跳:99999]")[1] is None


def test_daytime_is_clamped_to_5_to_55_minutes():
    assert B.delay_seconds(2, T("2026-10-10 14:00")) == 5 * 60
    assert B.delay_seconds(20, T("2026-10-10 14:00")) == 20 * 60
    assert B.delay_seconds(120, T("2026-10-10 14:00")) == 55 * 60
    # 01:59 还算白天（白天到次日 02:00）
    assert B.delay_seconds(120, T("2026-10-11 01:59")) == 55 * 60


def test_night_is_30_to_180_but_never_past_morning():
    assert B.delay_seconds(10, T("2026-10-11 03:00")) == 30 * 60
    assert B.delay_seconds(120, T("2026-10-11 03:00")) == 120 * 60
    # 07:00 写 180，落点越过 08:30 → 截到 08:30，也就是 90 分钟
    assert B.delay_seconds(180, T("2026-10-11 07:00")) == 90 * 60
    # 离 08:30 只差零点几秒也至少等 1 秒，不原地空转
    assert B.delay_seconds(60, T("2026-10-11 08:29:59.700000")) == 1


def test_hint_tells_him_the_range_for_now():
    day = B.hint(T("2026-10-10 14:00"))
    night = B.hint(T("2026-10-11 03:00"))
    assert "[心跳:N]" in day and "5–55" in day and "她看不到" in day
    assert "30–180" in night and "8:30" in night
