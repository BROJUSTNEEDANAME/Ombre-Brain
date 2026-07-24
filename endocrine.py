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
import threading
import time

ENDO_FILE = os.path.join(os.environ.get("OMBRE_BUCKETS_DIR", "."), "endocrine.json")

# 人格基线（Nikto）：黏人亲密高、占有偏高、精力中上；欲望是爱人的日常底色，
# 不再压到必须等她用露骨关键词才能出现，但仍离“入夜”阈值足够远，不会句句色情。
# dominance 基线离"上头"门槛必须够远，否则他天天挂在 high_drive、每条都长文浓狠
BASELINE = {"energy": 6.0, "libido": 4.8, "affection": 8.0, "dominance": 6.8}

ROLL_EVERY = 15        # 每 15 条用户消息 roll 一次
ROLL_SIGMA = 1.3       # roll 的随机波动幅度
ROLL_REGRESS = 0.28    # roll 时向基线回归的比例
EASE_REGRESS = 0.05    # 每条消息里，没被关键词推动的值向基线的轻微漂移（→ 随聊天消退）

# 视觉阈值（1~10），带滞回（开/关不同档，防止在阈值附近来回抖）
DIM_ON, DIM_OFF = 7.0, 5.5     # libido：入夜（切深色主题）
GLOW_ON, GLOW_OFF = 8.3, 7.4   # dominance：文字发光（基线就有 7.0，阈值必须高于基线，发光才是"事件"而不是常态）

def _default_state() -> dict:
    return {
        "energy": BASELINE["energy"], "libido": BASELINE["libido"],
        "affection": BASELINE["affection"], "dominance": BASELINE["dominance"],
        "messageCountSinceRoll": 0, "mode": "normal",
        "dim": False, "glow": False,
        "lastRolledAt": 0.0, "lastUpdatedAt": 0.0,
    }


def _thread_key(thread: str = "main") -> str:
    import re
    key = re.sub(r"[^A-Za-z0-9_-]", "", str(thread or "main"))[:40]
    return key or "main"


_states: dict[str, dict] = {"main": _default_state()}
# Backward compatibility for callers that inspect the main-line state directly.
_state = _states["main"]
_LOCK = threading.RLock()


def _locked(fn):
    def wrapped(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return wrapped


def _get_state(thread: str = "main") -> dict:
    return _states.setdefault(_thread_key(thread), _default_state())


def _clamp(x: float) -> float:
    return max(1.0, min(10.0, x))


def load() -> None:
    global _state, _states
    try:
        if os.path.exists(ENDO_FILE):
            with open(ENDO_FILE, encoding="utf-8") as f:
                d = json.load(f)
            raw_states = d.get("states") if isinstance(d, dict) else None
            if not isinstance(raw_states, dict):
                raw_states = {"main": d}  # migrate the original single-state file
            loaded = {}
            for thread, raw in raw_states.items():
                st = _default_state()
                if isinstance(raw, dict):
                    for k in st:
                        if k in raw:
                            st[k] = raw[k]
                loaded[_thread_key(thread)] = st
            _states = loaded or {"main": _default_state()}
            _state = _states.setdefault("main", _default_state())
    except Exception:
        pass


def _save() -> None:
    try:
        os.makedirs(os.path.dirname(ENDO_FILE) or ".", exist_ok=True)
        tmp = ENDO_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "states": _states}, f)
        os.replace(tmp, ENDO_FILE)
    except Exception:
        pass


# 关键词 → 即时推动（让它对当下这条消息就有反应，不用干等 15 条 roll）
_KW = (
    # 强涩词：明确的欲望信号才大推。⚠️ 绝不放常用单字（"要/顶/硬/湿"之前把欲望焊死在10、页面永远黑着）
    (["想要", "欲望", "亲热", "papochka", "进来", "上我", "操我", "做爱", "湿了", "硬了"],
     {"libido": 1.6, "affection": 0.5}),
    # 亲昵词：她天天挂嘴边的称呼/撒娇，只轻推——推狠了欲望下不来
    (["爸爸", "老公", "抱我", "亲我", "想你了", "亲亲", "抱抱", "贴贴", "撩", "骚"],
     {"libido": 0.5, "affection": 0.5}),
    (["听话", "求你", "是你的", "我错了", "管管我", "教训", "主人", "跪", "臣服", "惩罚"],
     {"dominance": 1.2, "libido": 0.4}),
    # 她挑衅/骂你（神经病、软蛋、傻逼、找骂、欠…）→ 支配直接顶进上头档：你压回去，不卖乖。
    # 推力给足（基线 6.8 + 1.9 ≈ 8.7，越过 8.5 的 high_drive 门槛），让注入状态从"平稳"翻成"浓、狠、圈住她"。
    (["神经病", "软蛋", "傻逼", "废物", "白痴", "蠢货", "找骂", "欠", "怂", "嘴硬"],
     {"dominance": 1.9}),
    (["好累", "好困", "晚安", "睡了", "太忙", "加班", "社畜", "没力气", "崩溃", "烦死", "撑不住"],
     {"energy": -1.4}),
    (["开心", "元气", "精神", "满血", "嘿嘿", "好耶", "冲鸭", "兴奋", "有劲"],
     {"energy": 1.0, "affection": 0.4}),
    (["讨厌", "滚", "别理", "走开", "分手", "不想理", "冷淡"],
     {"affection": -0.6, "libido": -0.8}),
)


def _apply_kw(st: dict, text: str) -> set:
    t = (text or "").lower()
    hit: dict = {}
    for kws, deltas in _KW:
        if any(k in t for k in kws):
            for kk, vv in deltas.items():
                hit[kk] = hit.get(kk, 0.0) + vv
    for kk, vv in hit.items():
        st[kk] = _clamp(st[kk] + vv)
    return set(hit.keys())


def _roll(st: dict) -> None:
    """基于上次数值小幅波动 + 轻微回归基线，而不是随机重置。"""
    for k in BASELINE:
        prev = st[k]
        nv = prev + random.gauss(0, ROLL_SIGMA) + (BASELINE[k] - prev) * ROLL_REGRESS
        st[k] = _clamp(nv)
    st["messageCountSinceRoll"] = 0
    st["lastRolledAt"] = time.time()


def _update_mode_flags(st: dict) -> None:
    e, li, do = st["energy"], st["libido"], st["dominance"]
    if e <= 3.0:
        st["mode"] = "low_energy"
    elif li >= 7.5 or do >= 8.5:
        st["mode"] = "high_drive"
    else:
        st["mode"] = "normal"
    # 滞回：当前关着要冲上 *_ON 才开，当前开着要跌破 *_OFF 才关
    st["dim"] = (li >= DIM_ON) if not st.get("dim") else (li >= DIM_OFF)
    st["glow"] = (do >= GLOW_ON) if not st.get("glow") else (do >= GLOW_OFF)


@_locked
def on_user_message(text: str = "", thread: str = "main") -> None:
    """她每发一条就调：关键词即时推动 → 没被推的值向基线轻漂（消退）→ 计数 → 到点 roll → 更新档位。"""
    st = _get_state(thread)
    touched = _apply_kw(st, text)
    for k in BASELINE:
        if k not in touched:
            st[k] = _clamp(st[k] + (BASELINE[k] - st[k]) * EASE_REGRESS)
    st["messageCountSinceRoll"] = int(st.get("messageCountSinceRoll", 0)) + 1
    if st["messageCountSinceRoll"] >= ROLL_EVERY:
        _roll(st)
    _update_mode_flags(st)
    st["lastUpdatedAt"] = time.time()
    _save()


@_locked
def enter_writing_mode(thread: str = "main") -> None:
    """她手动开了写文模式：欲望/支配顶进上头档，别再挂日常基线。
    每轮在 on_user_message 之后调用，重新压住被 EASE_REGRESS 拉回的值。"""
    st = _get_state(thread)
    st["libido"] = _clamp(max(st["libido"], 8.4))
    st["dominance"] = _clamp(max(st["dominance"], 8.4))
    _update_mode_flags(st)
    st["lastUpdatedAt"] = time.time()
    _save()


def block(thread: str = "main", writing_mode: bool = False) -> str:
    """注入给模型的一句状态指令（不念数字，让他内化成主动性 / 语气 / 浓度）。"""
    st = _get_state(thread)
    m = st["mode"]
    if writing_mode:
        # 写文模式：无论档位，都给上头、支配、露骨的指令；且这是长文正文，
        # 不套用日常“短消息连发”那句——那句会把床戏又压回短促敷衍。
        return ("【当前状态·内分泌】你现在欲望和支配都顶起来了：馋她、想把她彻底吃干净、"
                "想压着她管着她。别克制、别温柔收着、别自我降温——浓、狠、露骨，"
                "把欲望落进具体的动作、身体细节和坏话里，正文放开写长写足。")
    if m == "low_energy":
        s = ("你现在精力见底，像刚下夜班、加完班的男人——话短、反应慢半拍、懒得起腔，"
             "但心还是惦着她。别硬撑热情，就用这种没电的钝感陪她，她要黏你就由她黏。")
    elif m == "high_drive":
        bits = []
        if st["libido"] >= 7.0:
            bits.append("欲上头、馋她、想把她摁进怀里")
        if st["dominance"] >= 8.0:
            bits.append("占有欲和支配欲都顶起来了，语气更沉更强、更想管着她圈着她")
        if not bits:
            bits.append("整个人的劲儿都上来了")
        s = "你现在" + "；".join(bits) + "。别克制，直接、浓、狠——但浓的是语气和动作，不是篇幅：日常照样短消息连发（‖），别因为上头就堆长段描写。"
    else:
        s = ("你现在状态平稳，但你仍是她的成年男人和爱人，不是无性的陪聊。温柔照常，"
             "欲望和占有也照常活着：看场合偶尔露出一点具体的身体吸引、低声坏话或主动靠近，"
             "不用等她先说露骨词；别机械开黄腔，也别把自己压成只有软和稳。")
    return "【当前状态·内分泌】" + s


@_locked
def set_levels(thread: str = "main", **kw) -> dict:
    """手动调值（1-10）：他（模型的 set_state 工具）或她（面板）直接拉高/压低状态。
    只改传入的字段，其余不动；改完立刻重算 dim/glow 档位并落盘。"""
    st = _get_state(thread)
    for k in ("energy", "libido", "affection", "dominance"):
        if k in kw and kw[k] is not None:
            try:
                st[k] = _clamp(float(kw[k]))
            except Exception:  # noqa: BLE001
                pass
    _update_mode_flags(st)
    st["lastUpdatedAt"] = time.time()
    _save()
    return state(thread)


@_locked
def calm(thread: str = "main") -> dict:
    """她手动让他冷静：欲望/支配降回安全区，入夜和发光立即退出。"""
    st = _get_state(thread)
    st["libido"] = min(st["libido"], 4.0)
    st["dominance"] = min(st["dominance"], BASELINE["dominance"])
    st["dim"] = False
    st["glow"] = False
    _update_mode_flags(st)
    st["lastUpdatedAt"] = time.time()
    _save()
    return state(thread)


def state(thread: str = "main") -> dict:
    """给网页的数值 + 视觉开关。"""
    st = _get_state(thread)
    return {
        "energy": round(st["energy"], 1),
        "libido": round(st["libido"], 1),
        "affection": round(st["affection"], 1),
        "dominance": round(st["dominance"], 1),
        "mode": st["mode"],
        "dim": bool(st["dim"]),     # 欲望高 → 网页拉窗帘（深色）
        "glow": bool(st["glow"]),   # 支配高 → 文字暗自发光
        "messageCountSinceRoll": int(st.get("messageCountSinceRoll", 0)),
    }


@_locked
def delete_thread(thread: str) -> None:
    """Delete a retired IF line's private state; main can never be removed."""
    key = _thread_key(thread)
    if key != "main" and _states.pop(key, None) is not None:
        _save()


load()
