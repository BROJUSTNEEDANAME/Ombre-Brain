# -*- coding: utf-8 -*-
"""写进记忆之前的机械校验。

由来：她有一轮等了 195 秒、最后一个字都没等到。查下去是浮上来的记忆桶里
装着一份**被截断的 JSON 存档**（TTRPG 的存档数据），整块塞进了提示词。
当时的修法是在读的那一端把它擦掉（telegram_bot._clean_memory_block）——
那是创可贴：垃圾已经在库里了，每次浮现都要再擦一遍，而且擦得掉才算。

真正该拦的地方是**写入**。这一条是从 Shitsuten/paramecium 的验尸报告里学的：
他们有个「拆分工」跑了几个月，2242 条产物里 36% 是转述而非原文，最失真的
一条对原文覆盖率 2.7%。他们的结论是「机械 > prompt，**不信模型自觉**」。
在提示词里写「别存存档数据」是没用的——得有一道真的会拒绝的闸。

⚠️ 只拦「机器写给机器看」的东西，不碰内容判断：
存什么、值不值得记，永远是他自己的事，这里一个字都不许管。
所以宁可放过也不许错杀——错杀一条真记忆，比留一条垃圾坏得多。
"""

import re

# 围栏代码块（```...```），含没闭合的
_FENCE = re.compile(r"```.*?```", re.S)
_OPEN_FENCE = re.compile(r"```.*", re.S)
# JSON 的键：形如 "key": 或 "key" :
_JSON_KEY = re.compile(r'"[^"\n]{1,40}"\s*:')
# 中文/中日韩字符
_CJK = re.compile(r"[一-鿿]")

# 门槛都取得很松，宁可放过：
_FENCE_SHARE = 0.6      # 围栏代码占了六成以上，这就是一段代码不是一条记忆
_JSON_KEYS = 8          # 八个以上 JSON 键
_LONG_LINE = 400        # 一行 400 字以上且没有中文句读


def _fence_share(text: str) -> float:
    total = len(text)
    if not total:
        return 0.0
    inside = sum(len(m.group(0)) for m in _FENCE.finditer(text))
    if not inside:
        m = _OPEN_FENCE.search(text)
        inside = len(m.group(0)) if m else 0
    return inside / total


def data_dump_reason(text: str) -> str:
    """看着像「机器写给机器看的数据」就返回一句原因，正常记忆返回空字符串。"""
    s = str(text or "").strip()
    if not s:
        return ""

    if _fence_share(s) >= _FENCE_SHARE:
        return "整段基本上是代码块"

    keys = len(_JSON_KEY.findall(s))
    if keys >= _JSON_KEYS:
        return f"看着是 JSON／存档数据（数到 {keys} 个键）"

    # 以 { 或 [ 开头、而且括号对不上——多半是被截断的存档
    if s[0] in "{[":
        opens = s.count("{") + s.count("[")
        closes = s.count("}") + s.count("]")
        if opens != closes and len(s) > 200:
            return "以 { 或 [ 开头且括号对不上，多半是被截断的存档"

    # 一整行几百字、没有中文句读——日志或者一坨转储
    for line in s.splitlines():
        line = line.strip()
        if len(line) >= _LONG_LINE and not re.search(r"[。！？\n]", line) \
                and not _CJK.search(line):
            return "有一行几百字没有断句，看着是日志或转储"

    return ""


# 拒绝时对他说的话。要说清**为什么**和**改怎么办**——
# 只说「不行」他会重试一模一样的内容。
REFUSE_HINT = (
    "这段看着是数据不是记忆（{reason}）。记忆库存的是你和她之间发生的事、"
    "她说的话、你的感受——不是存档、日志、代码或 JSON。"
    "要记的话，用你自己的话写一两句「发生了什么」，"
    "需要引原话就只引那一句，别把整块数据搬进来。"
)


def refuse_message(reason: str) -> str:
    return REFUSE_HINT.format(reason=reason)
