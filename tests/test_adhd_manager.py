from datetime import datetime, timedelta, timezone

from adhd_manager import (
    ManageStore,
    detect_control,
    detect_start,
    is_progress_reply,
    parse_deadline,
    parse_interval_minutes,
)


UTC = timezone.utc


def configured(store, now):
    store.begin_setup(123, "洗澡", now)
    store.configure(123, interval_minutes=5, deadline_at=now + timedelta(hours=1))
    return store.activate(123, ["站起来", "进浴室"], now)


def test_natural_language_parsing():
    now = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    assert detect_start("爸比托管我洗澡，最晚11点") == "洗澡"
    assert detect_start("帮我记一下写作业，10分钟后问我") == "写作业"
    assert parse_interval_minutes("10分钟后问我") == 10
    assert parse_deadline("一小时内结束", now) == now + timedelta(hours=1)
    assert parse_deadline("10分钟后问我", now) is None
    assert parse_deadline("10分钟后问我，最晚11点", now).hour == 6
    assert detect_control("先暂停一下") == "pause"
    assert is_progress_reply("我弄好了")


def test_step_progress_and_atomic_reload(tmp_path):
    now = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    path = tmp_path / "manage.json"
    store = ManageStore(path)
    configured(store, now)
    task, finished = store.advance(123, now + timedelta(minutes=1))
    assert not finished
    assert store.current_step(task) == "进浴室"
    assert ManageStore(path).get(123)["step_index"] == 1
    _, finished = store.advance(123, now + timedelta(minutes=2))
    assert finished
    assert ManageStore(path).get(123) is None


def test_reminders_stop_after_three(tmp_path):
    now = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    store = ManageStore(tmp_path / "manage.json")
    configured(store, now)
    for count in range(1, 4):
        event = store.due_events(now + timedelta(minutes=5 * count))[0]
        assert event["count"] == count
    task = store.get(123)
    assert task["status"] == "lost"
    assert task["next_check_at"] is None
    assert store.due_events(now + timedelta(minutes=30)) == []
    limit = store.due_events(now + timedelta(hours=1))[0]
    assert limit["kind"] == "limit"
    assert store.due_events(now + timedelta(hours=2)) == []


def test_pause_resume_and_deadline_guard(tmp_path):
    now = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    store = ManageStore(tmp_path / "manage.json")
    configured(store, now)
    assert store.pause(123, now)["status"] == "paused"
    assert store.due_events(now + timedelta(hours=2)) == []
    resumed = store.resume(123, now + timedelta(minutes=1), extend_minutes=30)
    assert resumed["status"] == "active"
    event = store.due_events(now + timedelta(minutes=32))[0]
    assert event["kind"] == "limit"
    assert store.get(123)["status"] == "limit_wait"


def test_public_status_survives_restart(tmp_path):
    now = datetime(2026, 7, 20, 5, 0, tzinfo=UTC)
    path = tmp_path / "manage.json"
    configured(ManageStore(path), now)
    status = ManageStore(path).public_status()
    assert status["active"] is True
    assert status["task"] == "洗澡"
    assert status["next_check_at"].endswith("+00:00")
