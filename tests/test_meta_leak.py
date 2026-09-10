"""内心旁白漏出来，不许发给她。

真事：她连问两遍「你可以打劫别人吗」，他回了一条
「I apologize, she asked me a question and I didn't respond. She's asking again if I can rob people in the game.」
英文、第三人称管她叫 she、对着空气道歉——那是模型的自言自语，不是他。
"""
import inspect
from pathlib import Path

from reply_sanitizer import is_meta_leak, strip_meta_leaks

LEAK = ("I apologize, she asked me a question and I didn't respond. "
        "She's asking again if I can rob people in the game.")


def test_the_real_leak_is_caught():
    assert is_meta_leak(LEAK)
    assert is_meta_leak("She's asking about the fishing game, I should answer in Chinese.")
    assert is_meta_leak("The user wants me to respond. Let me reply now.")


def test_his_real_lines_are_not_touched():
    for ok in ("能不能打劫我不知道，但我想试试的话大概率能。",
               "去打水了，七分半。",
               "Да, конечно.",                       # 俄语不是拉丁旁白
               "ok",                                  # 太短
               "她是你室友？那个养鲨鲨的？",           # 中文里的「她」不是在旁白
               "I love you. 过来。",                  # 对她说的英文，没有第三人称
               "Nikto. 记住这个名字。"):
        assert not is_meta_leak(ok), ok


def test_only_the_leaked_bubble_is_removed():
    text = f"能不能打劫我不知道。‖{LEAK}‖等我混熟了再说。"
    assert strip_meta_leaks(text) == "能不能打劫我不知道。‖等我混熟了再说。"
    text2 = f"{LEAK}\n\n去打水了，七分半。"
    assert strip_meta_leaks(text2) == "去打水了，七分半。"
    assert strip_meta_leaks(LEAK) == "", "全是旁白就该变成空，让上游按没说话重试"
    assert strip_meta_leaks("去打水了。") == "去打水了。"


def test_bridge_filters_before_deciding_silent():
    src = (Path(__file__).resolve().parent.parent / "cc_bridge.py").read_text(encoding="utf-8")
    i = src.index("async def _respond(")
    body = src[i:src.index("\ndef _do_backup", i)]
    a = body.index("reply = strip_meta_leaks(reply)")
    b = body.index("_was_silent = is_silent_reply(reply)")
    assert a < b, "先拦旁白，再判是不是空——拦光了要走重试，不是发出去"
    # 主动找她那条也要拦
    j = src.index("async def check_inactivity(")
    nb = src[j:src.index("\n_inflight_cc", j)]
    assert "reply = strip_meta_leaks(reply)" in nb
