"""他自己定下次什么时候主动找她（心跳 B）。

由来 2026-10-10：她说「现在有的是定时 15 分钟不理唤醒，再加上一个自己决定
自己下次什么时候回复」，给的参考是 leiiiyurong/heartbeat（MIT）。思路照它：
他每次回复的最后一行写 [心跳:N]，N 分钟后桥来叫他；这一行发给她之前删掉。

这里全是纯函数，只回答「现在是几点、他写了 N，该等几秒」。不碰 Telegram、不碰模型，
接线在 cc_bridge.py。时间一律是她那边的本地时间（naive datetime）。

规矩（照参考仓库，踩过的坑都在）：
- 白天 08:30–次日 02:00：N 夹在 5–55 分钟。55 是因为提示缓存最长 1 小时，
  不超过它，每次叫醒他都还吃得到缓存。
- 夜里：N 放宽到 30–180，但落点越过早上 08:30 就截到 08:30。
- 他写的 N 不可信：只认 1–4 位数字，读出来再夹进范围；没写、写错，由调用方按默认处理。
- 这一行不管在哪种模式都要删，不能让她看到。
"""
from __future__ import annotations

import math
import re
from datetime import datetime, time as dtime, timedelta

DAY_START = dtime(8, 30)
DAY_END = dtime(2, 0)          # 跨零点
DAY_MIN, DAY_MAX = 5, 55       # 分钟
NIGHT_MIN, NIGHT_MAX = 30, 180

# 中英文都认，冒号全角半角都行，可以带「分钟」/ min
TAG_LINE_RE = re.compile(
    r"^[ \t]*\[(?:心跳|heartbeat)[ \t]*[:：][ \t]*(\d{1,4})[ \t]*(?:分钟|分|mins?|minutes?)?[ \t]*\][ \t]*$",
    re.I | re.M)


def in_day(t: dtime) -> bool:
    """t 在不在白天时段（08:30 到次日 02:00）。"""
    return t >= DAY_START or t <= DAY_END


def _next_day_start(now: datetime) -> datetime:
    start = datetime.combine(now.date(), DAY_START)
    return start if start > now else start + timedelta(days=1)


def pop_tag(reply: str) -> tuple[str, int | None]:
    """删掉回复里所有单独成行的 [心跳:N]，返回 (给她看的回复, 最后一个 N)。

    只删单独成行的：她自己说「[心跳:5]」这种话被他引用在句子中间，不动。"""
    if not reply:
        return reply, None
    found = TAG_LINE_RE.findall(reply)
    if not found:
        return reply, None
    clean = TAG_LINE_RE.sub("", reply)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, int(found[-1])


def delay_seconds(minutes: int, now: datetime) -> int:
    """把他写的 N 分钟换成真正要等的秒数（已夹好范围）。now 是她的本地时间。"""
    if in_day(now.time()):
        return min(DAY_MAX, max(DAY_MIN, minutes)) * 60
    wait = min(NIGHT_MAX, max(NIGHT_MIN, minutes)) * 60
    # 夜里落点越过早上起点就截到起点；向上取整、至少 1 秒，免得差零点几秒原地空转
    until_morning = max(1, math.ceil((_next_day_start(now) - now).total_seconds()))
    return min(wait, until_morning)


def hint(now: datetime) -> str:
    """附在发给他的消息末尾，告诉他怎么写标记。"""
    span = (f"{DAY_MIN}–{DAY_MAX}" if in_day(now.time())
            else f"{NIGHT_MIN}–{NIGHT_MAX}（落点过了早上 8:30 会截到 8:30）")
    return ("\n（桥的提示，不是她说的：回复的最后一行单独写 [心跳:N]，N 是分钟数，"
            f"意思是她要是一直不说话，你 N 分钟后再主动找她。现在能写 {span}。"
            "想她了就写小一点，她在忙、在睡就写大一点。这一行会被删掉，她看不到。）")
