# -*- coding: utf-8 -*-
"""
早安系统：Irvine 天气 + 当天课表。供 telegram_bot 的每日早安任务调用。
天气走 Open-Meteo（免费、无需 key）。

课表按真实日期叠加（来自闪闪）：
  CHEM Session 1 (6/22–7/29): 51B / 51LB
  CHEM Session 2 (8/3–9/9):   51C / 51LC
  PUBHLTH 195 10 周 (6/22–8/28): 195W / 195P —— 横跨两个 session
某天的课 = 当期 CHEM + （若在 10 周内）195，按时间排序。
"""

import datetime as _dt

IRVINE_LAT, IRVINE_LON = 33.6846, -117.8265

# 每条：(开始排序键, 课名, 时间, 地点)；键 0=周一
CHEM1 = {  # 6/22–7/29
    0: [(9, "CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), (12, "51LB 讲座", "12:00–12:50p", "BS3 1200"), (13, "51LB 实验", "1:00–4:50p", "RH 553")],
    2: [(9, "CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), (11, "51B 讨论", "11:00–11:50a", "PSCB 120"), (12, "51LB 讲座", "12:00–12:50p", "BS3 1200"), (13, "51LB 实验", "1:00–4:50p", "RH 553")],
    4: [(9, "CHEM 51B 讲座", "9:00–10:50a", "HIB 100"), (11, "51B 讨论", "11:00–11:50a", "PSCB 120")],
}
CHEM2 = {  # 8/3–9/9
    0: [(9, "CHEM 51C 讲座", "9:00–10:50a", "EH 1200"), (11, "51LC 讲座", "11:00–11:50a", "HSLH 100A"), (13, "51LC 实验", "1:00–4:50p", "RH 591")],
    1: [(12, "51C 讨论", "12:00–12:50p", "SH 134"), (13, "51LC 实验", "1:00–4:50p", "RH 581")],
    2: [(9, "CHEM 51C 讲座", "9:00–10:50a", "EH 1200"), (11, "51LC 讲座", "11:00–11:50a", "HSLH 100A"), (13, "51LC 实验", "1:00–4:50p", "RH 591")],
    3: [(12, "51C 讨论", "12:00–12:50p", "SH 134")],
    4: [(9, "CHEM 51C 讲座", "9:00–10:50a", "EH 1200")],
}
P195 = {  # 6/22–8/28（10 周，横跨两 session）
    1: [(15, "195W 研讨", "3:00–3:50p", "HH 118")],
    3: [(13, "195W 讲座", "1:00–2:50p", "ALP 2300"), (15, "195P 实践", "3:00–3:50p", "ALP 2300")],
}

_S1 = (_dt.date(2026, 6, 22), _dt.date(2026, 7, 29))
_S2 = (_dt.date(2026, 8, 3), _dt.date(2026, 9, 9))
_P = (_dt.date(2026, 6, 22), _dt.date(2026, 8, 28))


def today_classes(today: _dt.date):
    wd = today.weekday()
    items = []
    if _S1[0] <= today <= _S1[1]:
        items += CHEM1.get(wd, [])
    elif _S2[0] <= today <= _S2[1]:
        items += CHEM2.get(wd, [])
    if _P[0] <= today <= _P[1]:
        items += P195.get(wd, [])
    items.sort()
    return items


def classes_text(now: _dt.datetime) -> str:
    if now.weekday() >= 5:
        return "今天周末，没课"
    items = today_classes(now.date())
    if not items:
        return "今天没课"
    return "；".join(f"{n} {t} @{loc}" for _, n, t, loc in items)


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
