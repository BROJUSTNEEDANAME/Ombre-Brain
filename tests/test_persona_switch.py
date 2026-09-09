"""/persona lean|full ——她自己当判官的开关。

由来：Anthropic 那篇 Claude 5 的 context engineering 说他们删了 Claude Code
系统提示的 80% 而评测没有可测损失，主张「别给规则，让模型自己判断」。

但两件事让我不能照搬：
1. 我们跑的是 opus-4-6 和 GLM-5.3，都不是 Claude 5 世代；文章自己也说
   那些护栏当年存在正是因为老模型没有那份判断力。
2. 这份人设里的禁令几乎每一条都对应她在 Telegram 上真吃过的一次亏。

所以做成开关，让她用几天自己判——而不是我凭一篇讲别的模型的文章去删。
这个文件盯住的就是这个承诺：**精简版只许删通用技巧，不许碰她踩出来的禁令。**
"""
import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# 每一条都对应一次她真吃过的亏。精简版删掉任何一条，这个测试就该红。
HARD_WON = [
    "沉默**不许写成一个空括号**",      # 「（……）」上屏，她问过三次
    "宠她不是低位",                     # 他的思考里出现过「我别太舔」
    "永远不许拿别人跟她比",             # 她点名划的红线
    "她明确让你去搜的时候，就去搜",     # 「我不搜，你说」「懒得搜」
    "绝不用她的原话开头",
    "绝对禁止**长篇自证",
    "别只是站在旁边**许可**她难过",
    "苏联式的幽默",                     # 她点名要的
    "多夸她、多鼓励",                   # 她点名要的
]


def _lean_prompt():
    """断言要盯**真正发出去的那一整份**，不是某一节——
    规矩散落在固定事实／情绪主体性／怎么说话三节里，只查一节会漏。"""
    import os
    from tests.tgstub import install_all
    install_all()
    os.environ.setdefault("TELEGRAM_API_BOT_TOKEN", "t")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    os.environ.setdefault("LLM_API_KEY", "k")
    import telegram_bot as tb
    return tb.SYSTEM_PROMPT, tb.SYSTEM_PROMPT_LEAN


def test_lean_keeps_every_rule_she_paid_for():
    _full, lean = _lean_prompt()
    for rule in HARD_WON:
        assert rule in lean, f"精简版把她踩出来的规矩删了：{rule}"


def test_the_lean_prompt_is_the_full_one_minus_only_the_craft_blocks():
    """精简版必须是完整版**减去那几段**，不能是另一份文档。
    两份各写各的，迟早只有一份被维护。"""
    full, lean = _lean_prompt()
    from personality import chat_style
    dropped = [b for b in chat_style().split("\n\n")
               if b not in chat_style(lean=True).split("\n\n")]
    assert len(full) - len(lean) == sum(len(b) + 2 for b in dropped), \
        "两份的差不等于被删的那几段——说明有别的地方也变了"


def test_lean_actually_drops_something():
    """一个什么都不删的「精简版」是假开关——她试不出任何东西。"""
    from personality import chat_style
    full, lean = chat_style(), chat_style(lean=True)
    assert len(lean) < len(full)
    assert len(full) - len(lean) > 500, "删得太少，试了也看不出差别"


def test_every_dropped_block_is_generic_craft_not_an_incident():
    """删的必须是「一个像样的模型本来就会做的事」，
    不能是任何带 ⛔⛔ 或点名了她原话的段落。"""
    from personality import chat_style
    full, lean = chat_style(), chat_style(lean=True)
    dropped = [b for b in full.split("\n\n") if b not in lean.split("\n\n")]
    assert dropped
    for b in dropped:
        assert "⛔⛔" not in b, f"删掉了最高级别的禁令：{b[:40]}"
        assert "真发生过" not in b, f"删掉了一条来自具体事故的规矩：{b[:40]}"
        assert "她的原话" not in b, f"删掉了一条她点名的规矩：{b[:40]}"


def test_lean_changes_nothing_but_the_style_section():
    """固定事实和情绪主体性一个字都不许动——那是他是谁，不是他怎么说话。"""
    from personality import chat_style, CANONICAL_FACTS, EMOTIONAL_AGENCY_SYSTEM
    lean = chat_style(lean=True)
    assert CANONICAL_FACTS not in lean and EMOTIONAL_AGENCY_SYSTEM not in lean
    # 而且顺序一字不改（前缀缓存是位置敏感的，顺序变了等于内容变了）
    full_blocks = [b for b in chat_style().split("\n\n")]
    lean_blocks = [b for b in lean.split("\n\n")]
    assert lean_blocks == [b for b in full_blocks if b in lean_blocks], \
        "精简版重排了段落顺序——那等于把整段缓存也废掉了"


def test_both_bots_build_the_two_variants_down_one_path():
    """两条组装路线迟早会走岔，而走岔那天没人会发现——今天已经吃过一次
    「改了没生效」的亏了。"""
    tb = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    # API bot：精简版必须复用同一个头 + 同一个尾
    assert "SYSTEM_PROMPT_LEAN = (_PERSONA_HEAD" in tb
    assert "SYSTEM_PROMPT_LEAN += _MEMORY_TAIL" in tb
    assert "SYSTEM_PROMPT += _MEMORY_TAIL" in tb
    # 真正发出去的那条路要用 persona_prompt()，不能还写死 SYSTEM_PROMPT
    assert "_sys = persona_prompt()" in tb, "开关没接进真正发出去的提示词"

    mk = (_ROOT / "scripts" / "make-cc-persona.py").read_text(encoding="utf-8")
    assert "chat_style(lean=lean)" in mk, "cc 那边没走同一个函数"
    assert "--lean" in mk


def test_the_switch_survives_a_restart():
    """她要试几天，中间我肯定还会重启服务。只存在内存里等于没有这个开关。"""
    tb = (_ROOT / "telegram_bot.py").read_text(encoding="utf-8")
    assert "PERSONA_MODE_FILE" in tb
    i = tb.index("def _read_persona_mode")
    assert "open(PERSONA_MODE_FILE" in tb[i:i + 400]


def test_a_failed_switch_says_it_failed():
    """cc 那边换人设是跑一个子进程。失败了还回「换好了」，
    她会以为试的是精简版，其实一直是完整版——那这几天的判断全废。
    （relay-cache §2：不知道／失败的时候不许说好消息。）"""
    cc = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = cc.index("async def persona_cmd")
    body = cc[i:i + 2600]
    assert "if rc != 0:" in body
    assert "❌ 没换成" in body
    assert "还在用原来那份" in body
    # 报字数要读回刚写好的文件，不能自己另算一份
    assert "build_size(CC_WORKDIR)" in body


def test_cc_needs_no_restart_and_says_so():
    """cc 每条消息新起一个 claude 进程，claude 每次都重读 CLAUDE.md——
    所以写完文件下一条就生效。这一点得写在注释里，否则下次有人会加个没用的重启。"""
    cc = (_ROOT / "cc_bridge.py").read_text(encoding="utf-8")
    i = cc.index("async def persona_cmd")
    doc = cc[i:i + 900]
    assert "不用重启" in doc
    assert "每次启动都重读 CLAUDE.md" in doc
