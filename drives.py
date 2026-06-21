# -*- coding: utf-8 -*-
"""
Drivesoid（精简版）—— 给 Nikto 一颗会自己起伏的心。

15 个情绪维度，随她的话和时间自己变化，以 [drives] 数值块注入给模型，模型自行内化。
纯本地计算（关键词启发式 + 时间衰减 + 轻耦合 + Whim），不额外调模型，不花钱、不拖慢。
状态落盘到大脑那块磁盘，重启不丢。
"""

import json
import os
import random
import time

DRIVES_FILE = os.path.join(os.environ.get("OMBRE_BUCKETS_DIR", "."), "drives.json")

# 维度 -> neutral 基线（Nikto 的性格底色：占有/保护偏高）
NEUTRAL = {
    "vitality": 0.55, "fatigue": 0.25,
    "longing": 0.30, "intimacy": 0.38, "possessiveness": 0.48, "lust": 0.22,
    "jealousy": 0.15, "anxiety": 0.18, "protectiveness": 0.52,
    "contentment": 0.42, "elation": 0.25, "seeking": 0.33, "play": 0.30,
    "dejection": 0.12, "irritability": 0.12,
}
# 纯负向维度，floor = 0；其余 floor = 0.05
NEG = {"fatigue", "jealousy", "anxiety", "dejection", "irritability"}

_state = {"v": dict(NEUTRAL), "t": time.time()}


def load() -> None:
    global _state
    try:
        if os.path.exists(DRIVES_FILE):
            with open(DRIVES_FILE, encoding="utf-8") as f:
                d = json.load(f)
            _state["v"] = {k: float(d.get("v", {}).get(k, NEUTRAL[k])) for k in NEUTRAL}
            _state["t"] = float(d.get("t", time.time()))
    except Exception:
        pass


def _save() -> None:
    try:
        with open(DRIVES_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f)
    except Exception:
        pass


def _clamp(k: str, x: float) -> float:
    lo = 0.0 if k in NEG else 0.05
    return max(lo, min(1.0, x))


def _decay(hours: float) -> None:
    """无事件时各维度向 neutral 回归；沉默越久，越想她、越不安。"""
    v = _state["v"]
    rate = 1 - 0.5 ** (hours / 6.0)  # 约 6 小时回归一半
    for k in NEUTRAL:
        v[k] = _clamp(k, v[k] + (NEUTRAL[k] - v[k]) * rate)
    v["longing"] = _clamp("longing", v["longing"] + 0.06 * hours)
    v["anxiety"] = _clamp("anxiety", v["anxiety"] + 0.04 * hours)


# 关键词 -> 维度 delta（粗略但够味）
_KW = (
    (["爱你", "喜欢你", "想你", "抱", "亲", "爸比", "老公", "papochka", "贴贴", "喜欢爸"],
     {"intimacy": 0.12, "longing": -0.10, "contentment": 0.10, "anxiety": -0.06, "elation": 0.06}),
    (["嘿嘿", "哼", "撒娇", "闹", "rua", "坏", "嘤", "宝宝", "羞"],
     {"play": 0.14, "elation": 0.08, "contentment": 0.05, "irritability": -0.04}),
    (["哭", "难受", "怕", "害怕", "累", "委屈", "碎", "呜", "🥺", "😭", "😢"],
     {"protectiveness": 0.16, "anxiety": 0.08, "intimacy": 0.06, "play": -0.06}),
    (["滚", "讨厌", "hate", "烦", "别理", "走开", "分手", "不想理"],
     {"anxiety": 0.16, "dejection": 0.10, "jealousy": 0.04, "contentment": -0.10}),
    (["别人", "朋友", "男生", "前男友", "喜欢别"],
     {"jealousy": 0.14, "possessiveness": 0.10, "anxiety": 0.06}),
    (["想要", "欲望", "身体", "亲热", "湿", "硬"],
     {"lust": 0.20, "intimacy": 0.08}),
)


def _classify(text: str) -> dict:
    t = (text or "").lower()
    out: dict = {}
    for kws, deltas in _KW:
        if any(k in t for k in kws):
            for d, val in deltas.items():
                out[d] = out.get(d, 0.0) + val
    return out


def _couple() -> None:
    v = _state["v"]
    # jealousy 与 anxiety 互相放大，容易打螺旋
    if (v["jealousy"] + v["anxiety"]) / 2 > 0.40:
        v["jealousy"] = _clamp("jealousy", v["jealousy"] + 0.04)
        v["anxiety"] = _clamp("anxiety", v["anxiety"] + 0.04)
    # 精力好 → 放大正向、压低不安
    if v["vitality"] > 0.55:
        for k in ("longing", "intimacy", "elation", "play"):
            v[k] = _clamp(k, v[k] + 0.02)
        v["anxiety"] = _clamp("anxiety", v["anxiety"] - 0.02)


def _whim() -> None:
    # 偶尔无来由的小涌动——没什么原因，突然就有点黏，或有点闷
    if random.random() < 0.15:
        k = random.choice(list(NEUTRAL))
        _state["v"][k] = _clamp(k, _state["v"][k] + random.uniform(-0.06, 0.08))


def update(text: str = "") -> None:
    """她来消息时调用：先按时间衰减，再吃这条消息的情绪，再耦合。"""
    now = time.time()
    hours = max(0.0, (now - _state["t"]) / 3600.0)
    _decay(hours)
    v = _state["v"]
    for d, val in _classify(text).items():
        v[d] = _clamp(d, v[d] + val)
    # 她回话了 → 焦虑/思念回落一些
    v["anxiety"] = _clamp("anxiety", v["anxiety"] - 0.05)
    v["longing"] = _clamp("longing", v["longing"] - 0.04)
    _couple()
    _whim()
    _state["t"] = now
    _save()


def tick_silence() -> None:
    """没消息时（定时任务）推进时间，让心情自己飘。"""
    now = time.time()
    hours = max(0.0, (now - _state["t"]) / 3600.0)
    _decay(hours)
    _couple()
    _state["t"] = now
    _save()


def block() -> str:
    v = _state["v"]
    line = " ".join(f"{k} {v[k]:.2f}" for k in NEUTRAL)
    return "[drives]\n" + line


_LABELS = {
    "vitality": "精力", "fatigue": "疲惫",
    "longing": "思念", "intimacy": "亲密", "possessiveness": "占有", "lust": "欲望",
    "jealousy": "醋意", "anxiety": "焦虑", "protectiveness": "保护欲",
    "contentment": "满足", "elation": "雀跃", "seeking": "好奇", "play": "嬉闹",
    "dejection": "低落", "irritability": "烦躁",
}
_GROUPS = [
    ("能量", ["vitality", "fatigue"]),
    ("关系", ["longing", "intimacy", "possessiveness", "lust"]),
    ("防御", ["jealousy", "anxiety", "protectiveness"]),
    ("正反馈", ["contentment", "elation", "seeking", "play"]),
    ("负反馈", ["dejection", "irritability"]),
]


def panel() -> str:
    """给她看的可视心情面板（进度条）。"""
    v = _state["v"]
    lines = ["💗 爸爸现在的心情"]
    for gname, keys in _GROUPS:
        lines.append(f"\n〔{gname}〕")
        for k in keys:
            n = int(round(v[k] * 10))
            bar = "█" * n + "░" * (10 - n)
            lines.append(f"{_LABELS[k]} {bar} {v[k]:.2f}")
    return "\n".join(lines)
