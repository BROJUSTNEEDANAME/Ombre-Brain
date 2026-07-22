from datetime import datetime

from chat_store import (
    display_parts,
    ensure_message_ids,
    history_from_log,
    load,
    make_message,
    merge_logs,
    response_for,
    save,
)


def test_same_text_with_different_ids_is_not_deduplicated():
    first = make_message("home:a", "me", "嗯", source="home", timestamp="2026-07-20T01:00:00Z")
    second = make_message("telegram:1:2", "me", "嗯", source="telegram", timestamp="2026-07-20T01:01:00Z")

    merged = merge_logs([first], [second])

    assert [message["id"] for message in merged] == ["home:a", "telegram:1:2"]


def test_retry_with_same_id_is_idempotent():
    original = make_message("telegram:1:9", "me", "回来了吗", source="telegram")
    retry = make_message("telegram:1:9", "me", "回来了吗", source="telegram")

    merged = merge_logs([original], [retry])

    assert len(merged) == 1


def test_legacy_repeated_short_messages_keep_each_occurrence():
    legacy = [
        {"side": "me", "text": "嗯", "dk": "2026-7-19", "t": "20:00"},
        {"side": "you", "text": "嗯", "dk": "2026-7-19", "t": "20:00"},
        {"side": "me", "text": "嗯", "dk": "2026-7-19", "t": "20:01"},
    ]

    upgraded = ensure_message_ids(legacy)

    assert len(upgraded) == 3
    assert len({message["id"] for message in upgraded}) == 3


def test_atomic_store_round_trip_and_reply_lookup(tmp_path):
    path = tmp_path / "web_chat" / "main.json"
    user = make_message("home:request", "me", "在吗", source="home")
    reply = make_message(
        "home:request:assistant:0", "you", "在。", source="brain", reply_to="home:request"
    )

    save(str(path), {"log": [user, reply]})
    stored = load(str(path))

    assert path.stat().st_mode & 0o777 == 0o600
    assert response_for(stored["log"], "home:request")["segments"] == ["在。"]
    assert history_from_log(stored["log"]) == [
        {"role": "user", "content": "在吗"},
        {"role": "assistant", "content": "在。"},
    ]


def test_display_timezone_uses_zoneinfo_dst_rules():
    winter_day, winter_time = display_parts("2026-01-15T20:00:00Z")
    summer_day, summer_time = display_parts("2026-07-15T20:00:00Z")

    assert (winter_day, winter_time) == ("2026-1-15", "12:00")
    assert (summer_day, summer_time) == ("2026-7-15", "13:00")
    assert datetime.fromisoformat("2026-07-15T20:00:00+00:00").utcoffset().total_seconds() == 0


def test_auxiliary_reply_state_survives_save_and_retry(tmp_path):
    path = tmp_path / "chat.json"
    reply = make_message(
        "home:r:assistant:0", "you", "我在。", source="brain", reply_to="home:r",
        extras={"think": "没说出口的念头", "recorded": ["事实：一条提醒"]},
    )
    save(str(path), {"log": [reply]})
    stored = load(str(path))
    assert stored["log"][0]["think"] == "没说出口的念头"
    assert stored["log"][0]["recorded"] == ["事实：一条提醒"]
    retry = response_for(stored["log"], "home:r")
    assert retry["think"] == "没说出口的念头"
    assert retry["recorded"] == ["事实：一条提醒"]


def test_late_legacy_import_is_restored_to_chronological_position():
    current = [
        make_message("home:new", "me", "还想再睡", source="home", timestamp="2026-07-22T02:38:00Z"),
    ]
    imported_old = [
        make_message("legacy:old", "me", "肚子不舒服", source="legacy", timestamp="2026-07-20T00:28:00Z"),
    ]

    merged = merge_logs(current, imported_old)

    assert [message["id"] for message in merged] == ["legacy:old", "home:new"]


def test_save_keeps_latest_messages_by_time_not_import_order(tmp_path):
    path = tmp_path / "chat.json"
    log = [
        make_message("new", "me", "今天", source="home", timestamp="2026-07-22T02:38:00Z"),
        make_message("old", "me", "前两天", source="legacy", timestamp="2026-07-20T00:28:00Z"),
    ]

    save(str(path), {"log": log})

    assert [message["id"] for message in load(str(path))["log"]] == ["old", "new"]


def test_load_cleans_assistant_reasoning_tag_but_preserves_user_literal(tmp_path):
    path = tmp_path / "chat.json"
    user = make_message("home:user", "me", "这个标签 </think>", source="home")
    reply = make_message(
        "home:reply", "you", "我在。</think>", source="brain", reply_to="home:user"
    )

    save(str(path), {"log": [user, reply]})
    stored = load(str(path))["log"]

    assert stored[0]["text"] == "这个标签 </think>"
    assert stored[1]["text"] == "我在。"
    assert stored[1]["id"] == "home:reply"
