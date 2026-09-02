# -*- coding: utf-8 -*-
"""
早安系统：Irvine 天气。供 telegram_bot 的每日早安任务调用。
天气走 Open-Meteo（免费、无需 key）。

⚠️ 课表已按她的要求整个删掉（2026-09-02）。原来是写死在这个文件里的
2026 夏季课表，日期范围停在 9/9 和 8/28，她改了课表之后这里没跟着改，
他每天早上照着过期的念。写死的排程只会过期，不会自己更新——
以后要报课表，走记忆库（她说一句他记一条），不要再往代码里钉。
"""

import datetime as _dt

IRVINE_LAT, IRVINE_LON = 33.6846, -117.8265

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
