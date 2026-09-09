# -*- coding: utf-8 -*-
"""闪闪的身体数据落盘 + 读回。给 Nikto 看她的心率、睡眠、静息心率。

思路学自 KKarsyline/Collar_watch（Apple Watch → 服务器 → AI），
但**代码全部另写**——那个仓库是 PolyForm 非商业许可，一行都不抄。
这里只有标准库、纯函数 + 文件读写，没有框架。

为什么要它：人设里三处一直在等这个数据——
  · 他催她睡觉，但系统只告诉他「今晚催过几次」，不知道她到底睡没睡；
  · 世界书写着「她熬夜不睡，你就进不去梦」——他催睡的私心，现在纯靠猜；
  · 「她很容易焦虑，而且常常自己不知道」——HRV/静息心率恰恰是这个的客观信号。

⚠️ 两条硬边界：
  1. 这个模块**只碰 health 目录，绝不碰记忆桶**。写坏了最多丢几条心率。
  2. 数据可能是旧的（手表没联网、HAE 没跑）。读回时必须带上「多久之前的」，
     绝不把一小时前的心率当成此刻——CLAUDE.md 第五条：假的『现在』比没有更坏。
"""
from __future__ import annotations

import json
import os
import time

HEALTH_DIR = os.environ.get("OMBRE_HEALTH_DIR", os.path.expanduser("~/ombre-health"))

_ALLOWED = {
    "heart_rate", "resting_heart_rate", "heart_rate_variability",
    "respiratory_rate", "step_count", "active_energy",
    "sleep_hours", "wrist_temperature", "blood_oxygen",
}
_ALIAS = {
    "hr": "heart_rate", "bpm": "heart_rate",
    "resting": "resting_heart_rate", "rhr": "resting_heart_rate",
    "hrv": "heart_rate_variability", "sdnn": "heart_rate_variability",
    "resp": "respiratory_rate", "steps": "step_count",
    "spo2": "blood_oxygen", "sleep": "sleep_hours",
}
# 给 Nikto 看的中文名 + 单位
_LABEL = {
    "heart_rate": ("心率", "bpm"),
    "resting_heart_rate": ("静息心率", "bpm"),
    "heart_rate_variability": ("HRV", "ms"),
    "respiratory_rate": ("呼吸", "次/分"),
    "step_count": ("今日步数", "步"),
    "active_energy": ("活动能量", "kcal"),
    "sleep_hours": ("昨晚睡眠", "小时"),
    "wrist_temperature": ("腕温", "°C"),
    "blood_oxygen": ("血氧", "%"),
}


def _is_num(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def _count_keys(p) -> int:
    if isinstance(p, dict):
        return len(p.get("metrics", p))
    return 0


def _atomic_write(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _canon(name: str) -> str:
    n = str(name or "").strip().lower().replace(" ", "_")
    return _ALIAS.get(n, n)


def _latest_path() -> str:
    return os.path.join(HEALTH_DIR, "latest.json")


def _load_latest() -> dict:
    try:
        with open(_latest_path(), encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def ingest(payload) -> dict:
    """收一批样本，更新 latest.json，追加进当日 jsonl。只写 health 目录。

    认两种形状：
      {"metrics": [{"name": "heart_rate", "value": 82, "ts": 1700000000}, ...]}
      {"heart_rate": 82, "hrv": 45}
    """
    now = time.time()
    samples = []
    if isinstance(payload, dict) and isinstance(payload.get("metrics"), list):
        for m in payload["metrics"]:
            if not isinstance(m, dict):
                continue
            name = _canon(m.get("name", ""))
            val = m.get("value", m.get("qty"))
            raw_ts = m.get("ts", m.get("date", now))
            ts = float(raw_ts) if _is_num(raw_ts) else now
            if name in _ALLOWED and _is_num(val):
                samples.append((name, float(val), ts))
    elif isinstance(payload, dict):
        for k, v in payload.items():
            name = _canon(k)
            if name in _ALLOWED and _is_num(v):
                samples.append((name, float(v), now))

    if not samples:
        return {"accepted": 0, "rejected": _count_keys(payload),
                "reason": "没有可识别的指标"}

    os.makedirs(HEALTH_DIR, exist_ok=True)
    latest = _load_latest()
    for name, val, ts in samples:
        prev = latest.get(name)
        if not prev or ts >= prev.get("ts", 0):    # 只让更新的样本覆盖
            latest[name] = {"value": val, "ts": ts}
    _atomic_write(_latest_path(), latest)

    day = time.strftime("%Y%m%d", time.localtime(now))
    try:
        with open(os.path.join(HEALTH_DIR, f"samples-{day}.jsonl"),
                  "a", encoding="utf-8") as fh:
            for name, val, ts in samples:
                fh.write(json.dumps({"name": name, "value": val, "ts": ts}) + "\n")
    except OSError:
        pass

    return {"accepted": len(samples), "rejected": 0}


def _fmt_age(seconds: float) -> str:
    m = int(seconds // 60)
    if m < 1:
        return "刚刚"
    if m < 60:
        return f"{m} 分钟前"
    if m < 60 * 36:
        return f"{m // 60} 小时前"
    return f"{m // 1440} 天前"


def snapshot(max_age_h: float = 6.0) -> str:
    """给 Nikto 的一句话快照。**每一项都带「多久之前」**。
    整份数据都比 max_age_h 还旧，就返回空字符串——宁可不给，绝不把陈数据
    当成此刻塞给他（假的『现在』比没有更坏）。没有数据也返回空。
    """
    latest = _load_latest()
    if not latest:
        return ""
    now = time.time()
    freshest = min((now - v.get("ts", 0)) for v in latest.values()
                   if isinstance(v, dict))
    if freshest > max_age_h * 3600:
        return ""     # 整份都太旧，别注入

    parts = []
    for name in ("heart_rate", "resting_heart_rate", "heart_rate_variability",
                 "sleep_hours", "respiratory_rate", "step_count"):
        v = latest.get(name)
        if not isinstance(v, dict):
            continue
        age = now - v.get("ts", 0)
        if age > max_age_h * 3600:
            continue     # 单项太旧就跳过，不混进快照
        label, unit = _LABEL.get(name, (name, ""))
        val = v["value"]
        val_s = f"{val:.0f}" if float(val).is_integer() else f"{val:.1f}"
        parts.append(f"{label} {val_s}{unit}（{_fmt_age(age)}）")
    if not parts:
        return ""
    return "、".join(parts)


def detail(metric: str = "", hours: float = 24.0) -> str:
    """某个指标最近 hours 小时的逐点 + min/max/avg。给「往下钻」用。"""
    name = _canon(metric)
    if name not in _ALLOWED:
        return f"没有这个指标。能查的：{', '.join(sorted(_ALLOWED))}"
    now = time.time()
    cutoff = now - hours * 3600
    vals = []
    for day_off in (0, 1):
        day = time.strftime("%Y%m%d", time.localtime(now - day_off * 86400))
        try:
            with open(os.path.join(HEALTH_DIR, f"samples-{day}.jsonl"),
                      encoding="utf-8") as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if r.get("name") == name and r.get("ts", 0) >= cutoff:
                        vals.append(float(r["value"]))
        except OSError:
            continue
    if not vals:
        return f"最近 {hours:.0f} 小时没有{_LABEL.get(name, (name,))[0]}的记录。"
    label = _LABEL.get(name, (name, ""))[0]
    return (f"{label} 最近 {hours:.0f} 小时：{len(vals)} 个点，"
            f"最低 {min(vals):.0f}，最高 {max(vals):.0f}，"
            f"平均 {sum(vals) / len(vals):.0f}。")
