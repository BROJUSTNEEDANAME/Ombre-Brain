"""Deterministic ADHD task supervision state for the Telegram client."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo


UTC = timezone.utc
DEFAULT_TZ = ZoneInfo("America/Los_Angeles")
ACTIVE_STATUSES = {"setup", "active", "paused", "lost", "limit_wait"}

_START_PATTERNS = (
    re.compile(r"(?:请|你|爸爸|爸比)?(?:来)?(?:托管|监督|盯着|陪着|陪我|管着)我(?:做|去)?(?P<goal>.+)"),
    re.compile(r"帮我记一下(?:要|去|做)?(?P<goal>.+)"),
)
_CONTROL_WORDS = {
    "pause": ("暂停", "先停一下", "休息"),
    "resume": ("继续", "恢复托管", "接着来"),
    "skip": ("跳过", "下一项"),
    "replan": ("重新拆", "重拆", "换个步骤", "拆小一点"),
    "stop": ("结束托管", "停止托管", "不托管了", "结束任务", "结束", "停止"),
}
_CN_NUMBERS = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _number(value: str) -> int:
    if value.isdigit():
        return int(value)
    if value in _CN_NUMBERS:
        return _CN_NUMBERS[value]
    if len(value) == 2 and value.startswith("十"):
        return 10 + _CN_NUMBERS.get(value[1], 0)
    if len(value) == 2 and value.endswith("十"):
        return _CN_NUMBERS.get(value[0], 0) * 10
    return 0


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def local_time_label(value: str | None, tz: ZoneInfo = DEFAULT_TZ) -> str:
    parsed = parse_utc(value)
    return parsed.astimezone(tz).strftime("%H:%M") if parsed else "未设置"


def detect_start(text: str) -> str | None:
    cleaned = " ".join((text or "").strip().split())
    for pattern in _START_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            goal = match.group("goal").strip(" ，。,.？?")
            goal = re.split(
                r"(?:最晚|截止|在[\d一二两三四五六七八九十]+分钟|每[\d一二两三四五六七八九十]+分钟|[\d一二两三四五六七八九十]+分钟后)",
                goal,
                maxsplit=1,
            )[0]
            return goal.strip(" ，。,.？?") or None
    return None


def detect_control(text: str) -> str | None:
    compact = re.sub(r"[\s，。,.！？!?]", "", text or "")
    for action, words in _CONTROL_WORDS.items():
        if any(word in compact for word in words):
            return action
    return None


def is_progress_reply(text: str) -> bool:
    compact = re.sub(r"[\s，。,.！？!?]", "", text or "")
    if re.fullmatch(r"\d+", compact):
        return True
    if any(word in compact for word in ("没做", "没完成", "做不到", "不行", "卡住")):
        return False
    return any(
        word in compact
        for word in ("完成", "好了", "做完", "下一步", "搞定", "弄好了", "已经", "刚刚", "到了", "打开了", "站起来了", "拿好了")
    )


def parse_interval_minutes(text: str) -> int | None:
    patterns = (
        r"(?:每隔?|间隔|先等|过|在)?\s*(\d{1,3}|[一二两三四五六七八九十]{1,2})\s*分钟(?:后|检查|问|提醒)?",
        r"(\d{1,2}|[一二两三四五六七八九十]{1,2})\s*分(?:钟)?(?:后|检查|问|提醒)",
    )
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            return min(180, max(1, _number(match.group(1))))
    return None


def parse_deadline(text: str, now: datetime | None = None, tz: ZoneInfo = DEFAULT_TZ) -> datetime | None:
    now = (now or utc_now()).astimezone(tz)
    clock = re.search(r"(?:(?:最晚|截止|到|今晚|今天)\s*)?(\d{1,2})(?::|点半|点)(\d{1,2})?", text or "")
    if clock:
        hour = int(clock.group(1))
        minute = 30 if "点半" in clock.group(0) else int(clock.group(2) or 0)
        if hour <= 11 and ("今晚" in clock.group(0) or ("最晚" in clock.group(0) and now.hour >= 12)):
            hour += 12
        if hour <= 23 and minute <= 59:
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return candidate.astimezone(UTC)

    duration = re.search(
        r"(?:最晚|截止|限时|在)?\s*(\d{1,3}|[一二两三四五六七八九十]{1,2})\s*(分钟|小时)(?:内|后(?!问|检查|提醒)|结束|完成)",
        text or "",
    )
    if not duration:
        return None
    amount = _number(duration.group(1))
    delta = timedelta(minutes=amount) if duration.group(2) == "分钟" else timedelta(hours=amount)
    return (now + delta).astimezone(UTC)


def fallback_steps(goal: str) -> list[str]:
    templates = (
        (("洗澡", "洗漱"), ["先站起来。完成后回1", "拿好换洗衣服，走到浴室。完成后回1", "打开水，把手机留在外面。完成后回1", "现在洗完并擦干。完成后回1"]),
        (("作业", "写作", "论文", "学习"), ["先把要用的文件或书打开。完成后回1", "只写下第一句或第一道题。完成后回1", "继续专注十分钟，只做眼前这一小段。完成后回1", "保存现在的进度。完成后回1"]),
        (("房间", "收拾", "整理"), ["先拿一个垃圾袋站起来。完成后回1", "只捡眼前能看到的垃圾。完成后回1", "把散落的衣服放到同一个地方。完成后回1", "清出一小块桌面或地面。完成后回1"]),
    )
    for keywords, steps in templates:
        if any(word in goal for word in keywords):
            return steps
    return [
        f"先站起来，把「{goal}」需要的东西放到眼前。完成后回1",
        f"现在只做「{goal}」最小的一步。完成后回1",
        "继续做眼前这一小段，不想后面的。完成后回1",
        "停一下，确认成果并收尾。完成后回1",
    ]


class ManageStore:
    """Small atomic JSON store; its lock never covers LLM or network work."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._tasks: dict[str, dict] = {}
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self._tasks = data.get("tasks", {}) if isinstance(data, dict) else {}
            except FileNotFoundError:
                self._tasks = {}
            except (OSError, json.JSONDecodeError):
                self._tasks = {}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".adhd-manage-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"version": 1, "tasks": self._tasks}, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def get(self, chat_id: int) -> dict | None:
        with self._lock:
            task = self._tasks.get(str(chat_id))
            return deepcopy(task) if task and task.get("status") in ACTIVE_STATUSES else None

    def all_current(self) -> list[dict]:
        with self._lock:
            return [deepcopy(task) for task in self._tasks.values() if task.get("status") in ACTIVE_STATUSES]

    def put(self, task: dict) -> dict:
        with self._lock:
            task = deepcopy(task)
            task["updated_at"] = iso_utc(utc_now())
            self._tasks[str(task["chat_id"])] = task
            self._save_locked()
            return deepcopy(task)

    def begin_setup(self, chat_id: int, goal: str | None = None, now: datetime | None = None) -> dict:
        now = now or utc_now()
        return self.put({
            "id": str(uuid.uuid4()), "chat_id": chat_id, "goal": goal or "", "status": "setup",
            "steps": [], "step_index": 0, "interval_minutes": None, "deadline_at": None,
            "next_check_at": None, "reminder_count": 0, "created_at": iso_utc(now),
        })

    def configure(self, chat_id: int, *, goal: str | None = None, interval_minutes: int | None = None,
                  deadline_at: datetime | None = None) -> dict:
        task = self.get(chat_id) or self.begin_setup(chat_id, goal)
        if goal:
            task["goal"] = goal.strip()
        if interval_minutes:
            task["interval_minutes"] = min(180, max(1, int(interval_minutes)))
        if deadline_at:
            task["deadline_at"] = iso_utc(deadline_at)
        return self.put(task)

    def activate(self, chat_id: int, steps: Iterable[str], now: datetime | None = None) -> dict:
        now = now or utc_now()
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        cleaned = [str(step).strip() for step in steps if str(step).strip()]
        task.update({
            "steps": cleaned or fallback_steps(task["goal"]), "step_index": 0, "status": "active",
            "reminder_count": 0, "next_check_at": iso_utc(now + timedelta(minutes=task["interval_minutes"])),
            "started_at": task.get("started_at") or iso_utc(now), "last_user_at": iso_utc(now),
        })
        return self.put(task)

    def current_step(self, task: dict) -> str:
        steps = task.get("steps") or fallback_steps(task.get("goal", "任务"))
        index = min(int(task.get("step_index", 0)), len(steps) - 1)
        return steps[index]

    def advance(self, chat_id: int, now: datetime | None = None, skip: bool = False) -> tuple[dict, bool]:
        now = now or utc_now()
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        task["step_index"] = int(task.get("step_index", 0)) + 1
        finished = task["step_index"] >= len(task.get("steps") or [])
        if finished:
            task.update({"status": "completed", "ended_at": iso_utc(now), "next_check_at": None})
        else:
            task.update({
                "status": "active", "reminder_count": 0, "last_user_at": iso_utc(now),
                "next_check_at": iso_utc(now + timedelta(minutes=task["interval_minutes"])),
            })
        task["last_action"] = "skip" if skip else "complete"
        return self.put(task), finished

    def pause(self, chat_id: int, now: datetime | None = None) -> dict:
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        task.update({"status": "paused", "paused_at": iso_utc(now or utc_now()), "next_check_at": None})
        return self.put(task)

    def resume(self, chat_id: int, now: datetime | None = None, extend_minutes: int = 0) -> dict:
        now = now or utc_now()
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        if extend_minutes:
            task["deadline_at"] = iso_utc(now + timedelta(minutes=extend_minutes))
        task.update({
            "status": "active", "reminder_count": 0, "last_user_at": iso_utc(now),
            "next_check_at": iso_utc(now + timedelta(minutes=task["interval_minutes"])),
        })
        return self.put(task)

    def replace_steps(self, chat_id: int, steps: Iterable[str], now: datetime | None = None) -> dict:
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        task["steps"] = [str(step).strip() for step in steps if str(step).strip()]
        task["step_index"] = 0
        return self.resume(chat_id, now=now)

    def end(self, chat_id: int, reason: str = "stopped", now: datetime | None = None) -> dict:
        task = self.get(chat_id)
        if not task:
            raise KeyError(chat_id)
        task.update({"status": reason, "ended_at": iso_utc(now or utc_now()), "next_check_at": None})
        return self.put(task)

    def due_events(self, now: datetime | None = None) -> list[dict]:
        now = now or utc_now()
        events: list[dict] = []
        with self._lock:
            changed = False
            for key, task in self._tasks.items():
                if task.get("status") not in {"active", "lost"}:
                    continue
                deadline = parse_utc(task.get("deadline_at"))
                if deadline and now >= deadline:
                    task.update({"status": "limit_wait", "next_check_at": None, "updated_at": iso_utc(now)})
                    events.append({"kind": "limit", "task": deepcopy(task)})
                    changed = True
                    continue
                check_at = parse_utc(task.get("next_check_at"))
                if task.get("status") == "active" and check_at and now >= check_at:
                    count = int(task.get("reminder_count", 0)) + 1
                    task["reminder_count"] = count
                    task["updated_at"] = iso_utc(now)
                    if count >= 3:
                        task.update({"status": "lost", "next_check_at": None})
                    else:
                        task["next_check_at"] = iso_utc(now + timedelta(minutes=task["interval_minutes"]))
                    events.append({"kind": "reminder", "count": count, "task": deepcopy(task)})
                    changed = True
                self._tasks[key] = task
            if changed:
                self._save_locked()
        return events

    def public_status(self) -> dict:
        current = sorted(self.all_current(), key=lambda item: item.get("updated_at", ""), reverse=True)
        if not current:
            return {"active": False}
        task = current[0]
        return {
            "active": True,
            "task": task.get("goal", ""),
            "status": task.get("status", ""),
            "next_check_at": task.get("next_check_at"),
            "deadline_at": task.get("deadline_at"),
        }
