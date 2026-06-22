# -*- coding: utf-8 -*-
"""
早安系统：Irvine 天气 + 当天课表。供 telegram_bot 的每日早安任务调用。
天气走 Open-Meteo（免费、无需 key）。课表按 Session I / II 切换。
"""

import datetime as _dt
import os

IRVINE_LAT, IRVINE_LON = 33.6846, -117.8265

# 周几(0=Mon) -> [(课, 时间, 地点), ...]  —— 来自闪闪的真实课表
SESSION1 = {
    0: [("CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), ("51LB 讲座", "12:00–12:50p", "BS3 1200"), ("51LB 实验", "1:00–4:50p", "RH 553")],
    1: [("PUBHLTH 195W 研讨", "3:00–3:50p", "HH 118")],
    2: [("CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), ("51B 讨论", "11:00–11:50a", "PSCB 120"), ("51LB 讲座", "12:00–12:50p", "BS3 1200"), ("51LB 实验", "1:00–4:50p", "RH 553")],
    3: [("195W 讲座", "1:00–2:50p", "ALP 2300"), ("195P 实践", "3:00–3:50p", "ALP 2300")],
    4: [("CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), ("51B 讨论", "11:00–11:50a", "PSCB 120")],
}
SESSION2 = {
    0: [("CHEM 51C 讲座", "9:00–10:50a", "EH 1200"), ("51LC 讲座", "11:00–11:50a", "HSLH 100A"), ("51LC 实验", "1:00–4:50p", "RH 591")],
    1: [("51C 讨论", "12:00–12:50p", "SH 134"), ("51LC 实验", "1:00–4:50p", "RH 581"), ("195W 研讨", "3:00–3:50p", "HH 118")],
    2: [("CHEM 51C 讲座", "9:00–10:50a", "EH 1200"), ("51LC 讲座", "11:00–11:50a", "HSLH 100A"), ("51LC 实验", "1:00–4:50p", "RH 591")],
    3: [("51C 讨论", "12:00–12:50p", "SH 134"), ("195W 讲座", "1:00–2:50p", "ALP 2300"), ("195P 实践", "3:00–3:50p", "ALP 2300")],
    4: [("CHEM 51C 讲座", "9:00–10:50a", "EH 1200")],
}


def _term(today: _dt.date):
    forced = os.environ.get("OMBRE_TERM", "").strip()
    if forced == "1":
        return SESSION1
    if forced == "2":
        return SESSION2
    try:
        s1_end = _dt.date.fromisoformat(os.environ.get("OMBRE_S1_END", "2026-08-01"))
    except Exception:
        s1_end = _dt.date(2026, 8, 1)
    return SESSION1 if today <= s1_end else SESSION2


def classes_text(now: _dt.datetime) -> str:
    wd = now.weekday()
    if wd >= 5:
        return "今天周末，没课"
    cs = _term(now.date()).get(wd, [])
    if not cs:
        return "今天没课"
    return "；".join(f"{n} {t} @{loc}" for n, t, loc in cs)


_WCODE = {
    0: "晴", 1: "大致晴", 2: "局部多云", 3: "阴", 45: "雾", 48: "雾凇",
    51: "小毛毛雨", 53: "毛毛雨", 55: "大毛毛雨", 56: "冻毛毛雨", 57: "冻毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨", 66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨", 85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "强雷阵雨伴冰雹",
}


async def fetch_weather() -> str:
    import httpx  # Render 上随 anthropic/server 一起装好

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={IRVINE_LAT}&longitude={IRVINE_LON}"
        "&daily=temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max"
        "&timezone=America/Los_Angeles&temperature_unit=fahrenheit"
    )
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(url)
        d = r.json()["daily"]
    hi_f = d["temperature_2m_max"][0]
    lo_f = d["temperature_2m_min"][0]
    code = d["weather_code"][0]
    pop = d["precipitation_probability_max"][0]
    hi_c = round((hi_f - 32) * 5 / 9)
    lo_c = round((lo_f - 32) * 5 / 9)
    desc = _WCODE.get(int(code), "多云")
    return f"{desc}，{lo_c}–{hi_c}°C（{round(lo_f)}–{round(hi_f)}°F），降水概率 {pop}%"
