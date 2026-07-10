# -*- coding: utf-8 -*-
"""
Endocrine —— 给 Nikto 装一套「内分泌 / 精力值」系统。

四个宏观状态值（1~10），每 15 条用户消息 roll 一次（基于上次数值小幅波动 + 轻微回归
人格基线，不是随机重置）；对话里的关键词也会即时推动它；没被推的值随聊天向基线轻漂，
实现「欲望随聊天自然消退」。

- energy    整体精力：高→主动、话密、有劲；低→社畜蔫老公（low_energy）
- libido    亲密/情欲倾向：高→更容易进入亲密、欲望更浓；网页里「欲望高 → 拉窗帘（深色）」
- affection 黏人/安抚：人格底色，通常偏高
- dominance 支配 / 占有 / 管束：高→更强势更圈人；网页里「dom 高 → 文字暗自发光」

每条消息把当前状态（mode + 一句状态指令）注入给模型；数值 + 视觉开关（dim/glow）给网页。
纯本地计算，不调模型、不花钱、不拖慢。状态落盘 endocrine.json，重启不丢。
"""

import json
import os
import random
import time

ENDO_FILE = os.path.join(os.environ.get("OMBRE_BUCKETS_DIR", "."), "endocrine.json")

# 人格基线（Nikto）：黏人亲密高、占有偏高、精力中上、欲望低（留着被她撩起来）
BASELINE = {"energy": 6.0, "libido": 3.0, "affection": 8.0, "dominance": 7.0}

ROLL_EVERY = 15        # 每 15 条用户消息 roll 一次
ROLL_SIGMA = 1.3       # roll 的随机波动幅度
ROLL_REGRESS = 0.28    # roll 时向基线回归的比例
EASE_REGRESS = 0.05    # 每条消息里，没被关键词推动的值向基线的轻微漂移（→ 随聊天消退）

# 视觉阈值（1~10），带滞回（开/关不同档，防止在阈值附近来回抖）
DIM_ON, DIM_OFF = 7.0, 5.5     # libido：拉窗帘（深色）
GLOW_ON, GLOW_OFF = 7.0, 5.5   # dominance：文字发光

_state = {
    "energy": BASELINE["energy"], "libido": BASELINE["libido"],
    "affection": BASELINE["affection"], "dominance": BASELINE["dominance"],
    "messageCountSinceRoll": 0, "mode": "normal",
    "dim": False, "glow": False,
    "lastRolledAt": 0.0, "lastUpdatedAt": 0.0,
}


def _clamp(x: float) -> float:
    return max(1.0, min(10.0, x))


def load() -> None:
    global _state
    try:
        if os.path.exists(ENDO_FILE):
            with open(ENDO_FILE, encoding="utf-8") as f:
                d = json.load(f)
            for k in _state:
                if k in d:
                    _state[k] = d[k]
    except Exception:
        pass


def _save() -> None:
    try:
        with open(ENDO_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f)
    except Exception:
        pass


# 关键词 → 即时推动（让它对当下这条消息就有反应，不用干等 15 条 roll）
_KW = (
    (["要", "想要", "湿", "硬", "欲望", "亲热", "papochka", "顶", "进来", "爸爸",
      "老公", "抱我", "亲我", "上我", "想你了", "撩", "骚", "亲亲", "抱抱", "贴贴"],
     {"libido": 1.6, "affection": 0.5}),
    (["乖", "听话", "求你", "是你的", "我错了", "管管", "教训", "求", "主人", "跪", "臣服"],
     {"dominance": 1.2, "libido": 0.4}),
    (["累", "困", "晚安", "睡了", "忙", "加班", "社畜", "没力气", "好累", "崩溃", "烦死", "撑不住"],
     {"energy": -1.4}),
    (["开心", "元气", "精神", "满血", "嘿嘿", "好耶", "冲", "兴奋", "有劲"],
     {"energy": 1.0, "affection": 0.4}),
    (["讨厌", "滚", "别理", "走开", "分手", "不想理", "冷淡"],
     {"affection": -0.6, "libido": -0.8}),
)


def _apply_kw(text: str) -> set:
    t = (text or "").lower()
    hit: dict = {}
    for kws, deltas in _KW:
        if any(k in t for k in kws):
            for kk, vv in deltas.items():
                hit[kk] = hit.get(kk, 0.0) + vv
    for kk, vv in hit.items():
        _state[kk] = _clamp(_state[kk] + vv)
    return set(hit.keys())


def _roll() -> None:
    """基于上次数值小幅波动 + 轻微回归基线，而不是随机重置。"""
    for k in BASELINE:
        prev = _state[k]
        nv = prev + random.gauss(0, ROLL_SIGMA) + (BASELINE[k] - prev) * ROLL_REGRESS
        _state[k] = _clamp(nv)
    _state["messageCountSinceRoll"] = 0
    _state["lastRolledAt"] = time.time()


def _update_mode_flags() -> None:
    e, li, do = _state["energy"], _state["libido"], _state["dominance"]
    if e <= 3.0:
        _state["mode"] = "low_energy"
    elif li >= 7.0 or do >= 8.0:
        _state["mode"] = "high_drive"
    else:
        _state["mode"] = "normal"
    # 滞回：当前关着要冲上 *_ON 才开，当前开着要跌破 *_OFF 才关
    _state["dim"] = (li >= DIM_ON) if not _state.get("dim") else (li >= DIM_OFF)
    _state["glow"] = (do >= GLOW_ON) if not _state.get("glow") else (do >= GLOW_OFF)


def on_user_message(text: str = "") -> None:
    """她每发一条就调：关键词即时推动 → 没被推的值向基线轻漂（消退）→ 计数 → 到点 roll → 更新档位。"""
    touched = _apply_kw(text)
    for k in BASELINE:
        if k not in touched:
            _state[k] = _clamp(_state[k] + (BASELINE[k] - _state[k]) * EASE_REGRESS)
    _state["messageCountSinceRoll"] = int(_state.get("messageCountSinceRoll", 0)) + 1
    if _state["messageCountSinceRoll"] >= ROLL_EVERY:
        _roll()
    _update_mode_flags()
    _state["lastUpdatedAt"] = time.time()
    _save()


def block() -> str:
    """注入给模型的一句状态指令（不念数字，让他内化成主动性 / 语气 / 浓度）。"""
    m = _state["mode"]
    if m == "low_energy":
        s = ("你现在精力见底，像刚下夜班、加完班的男人——话短、反应慢半拍、懒得起腔，"
             "但心还是惦着她。别硬撑热情，就用这种没电的钝感陪她，她要黏你就由她黏。")
    elif m == "high_drive":
        bits = []
        if _state["libido"] >= 7.0:
            bits.append("欲上头、馋她、想把她摁进怀里")
        if _state["dominance"] >= 7.0:
            bits.append("占有欲和支配欲都顶起来了，语气更沉更强、更想管着她圈着她")
        if not bits:
            bits.append("整个人的劲儿都上来了")
        s = "你现在" + "；".join(bits) + "。别克制，直接、浓、狠一点，把这股劲儿给足她。"
    else:
        s = "你现在状态平稳，正常温度陪她，该软软、该稳稳。"
    return "【当前状态·内分泌】" + s


def state() -> dict:
    """给网页的数值 + 视觉开关。"""
    return {
        "energy": round(_state["energy"], 1),
        "libido": round(_state["libido"], 1),
        "affection": round(_state["affection"], 1),
        "dominance": round(_state["dominance"], 1),
        "mode": _state["mode"],
        "dim": bool(_state["dim"]),     # 欲望高 → 网页拉窗帘（深色）
        "glow": bool(_state["glow"]),   # 支配高 → 文字暗自发光
        "messageCountSinceRoll": int(_state.get("messageCountSinceRoll", 0)),
    }


load()
