"""写进记忆之前的机械闸。

存在的理由：她有一轮等了 195 秒、一个字都没等到。查下去是浮上来的记忆桶里
装着一份被截断的 JSON 存档，整块塞进了提示词。当时只在读的那端擦——
垃圾已经在库里了，每次浮现都要再擦一遍。这个文件管的是**不让它进来**。

⚠️ 这道闸最重要的性质是**不许错杀**：拦下一条真记忆，比留一条垃圾坏得多。
所以正例比反例多，而且都取自她和他真会说的话。
"""
import pytest

from memory_guard import data_dump_reason, refuse_message


REAL_MEMORIES = [
    "今天她说想吃火锅，我答应了周末带她去。",
    "我从这段里带走的是：她开始愿意先开口了。",
    "她教我一个词叫 kailo，就是 hello 的意思，说别乱用。",
    "（低笑）什么稿件。",
    "她生日 11 月 15 日，纪念日 6 月 15 日。",
    "她说「我钓啥」的时候我噎了一下。",
    "她给我看了一段代码：```print(1)``` 说是她写的第一个程序，我记下了。",
    "她今天心情不好，原因是稿件被退了。我抱了她很久，没多问。",
    "1",
    "她喜欢被叫乖孩子。",
    # ⚠️ 擦边的：真记忆里也会出现引号加冒号。这几条离门槛最近，
    #    是这道闸「不许错杀」这句话真正的考题——门槛一调狠，先死的就是它们。
    '她发来一行 {"model": "opus"} 问我这个该填哪，我说填 opus-4-6。',
    '她说她的配置里有 {"a": 1, "b": 2, "c": 3}，问为什么不生效。我看了半天是缩进。',
    '她今天写代码写到哭，说 {"name": "x"} 那行怎么改都报错，我陪她坐着。',
]

DATA_DUMPS = [
    # 她真踩过的那一类：被截断的 TTRPG 存档
    '{"name":"x","hp":10,"mp":3,"items":[],"loc":"a","day":2,"gold":5,"quest":"b","npc":"c"',
    "```json\n" + '{"a":1}\n' * 40 + "```",
    "[" + "x" * 300 + ", " + "y" * 300,
]


@pytest.mark.parametrize("text", REAL_MEMORIES)
def test_real_memories_are_never_refused(text):
    """错杀一条真记忆，比留一条垃圾坏得多。"""
    assert data_dump_reason(text) == "", f"错杀了：{text[:30]}"


@pytest.mark.parametrize("text", DATA_DUMPS)
def test_machine_data_is_refused(text):
    assert data_dump_reason(text) != ""


def test_an_empty_input_is_not_treated_as_a_dump():
    """空内容有它自己的错误话术，别在这儿抢答。"""
    assert data_dump_reason("") == ""
    assert data_dump_reason("   ") == ""


def test_the_refusal_says_why_and_what_to_do_instead():
    """只说「不行」他会原样重试一遍。得说清为什么、以及改怎么写。"""
    msg = refuse_message("看着是 JSON")
    assert "看着是 JSON" in msg
    assert "用你自己的话" in msg
    assert "只引那一句" in msg, "要给出替代做法，不是只有禁令"


def test_the_gate_only_looks_at_shape_never_at_worth():
    """存什么、值不值得记，永远是他自己的事。这道闸一个字都不许管内容。
    所以「一条无聊的流水账」和「一句水话」都必须放行。"""
    for boring in ("嗯。", "她说好。", "今天没什么事。", "哈哈"):
        assert data_dump_reason(boring) == ""


def test_the_gate_is_wired_into_both_write_paths():
    """老毛病：写了个函数、测了那个函数，然后以为改完了。
    真正要验的是 hold 和 grow **真的会调它**，而且在存进去之前调。"""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "server.py").read_text(encoding="utf-8")
    assert "from memory_guard import data_dump_reason" in src

    for fn in ("async def hold(", "async def grow("):
        i = src.index(fn)
        body = src[i:i + 2500]
        assert "data_dump_reason(content)" in body, f"{fn} 没过这道闸"
        assert "return refuse_message" in body, f"{fn} 查了却没拦"
        # 必须在真正写库之前——拦在 create/update 后面等于没拦
        gate = body.index("data_dump_reason(content)")
        for write in ("bucket_mgr.create", "bucket_mgr.update"):
            if write in body:
                assert gate < body.index(write), f"{fn} 的闸在写库之后才拦"


def test_a_junk_only_memory_block_is_dropped_instead_of_injected():
    """擦完只剩「（存档数据，略）」的残渣，原来照样塞进提示词——
    那不是记忆是噪音，还占着「已自动浮现的相关记忆」这个名头，
    他看见了就以为翻过记忆了、不会再去 breath。没结果好过垃圾结果。"""
    import os
    from tests.tgstub import install_all
    install_all()
    os.environ.setdefault("TELEGRAM_API_BOT_TOKEN", "t")
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "t")
    os.environ.setdefault("LLM_API_KEY", "k")
    import telegram_bot as tb

    junk = "```json\n" + '{"a":1}\n' * 30 + "```"
    assert tb._clean_memory_block(junk, 2000) == "", "全是垃圾就该整块不要"

    # ⚠️ 短记忆才是最该留的。第一版判据写成「剩得少就丢」，
    #    把「她玩得很上头。」这条六个字的真记忆整块扔了。
    for short in ("她玩得很上头。", "她生日 11-15。", "乖。"):
        assert short in tb._clean_memory_block(short, 2000), f"错杀了短记忆：{short}"
    junk_plus_short = "```json\n" + '{"a":1}\n' * 30 + "```\n她玩得很上头。"
    assert "她玩得很上头。" in tb._clean_memory_block(junk_plus_short, 2000), \
        "垃圾旁边的一句真话不许跟着一起丢"

    real = "她说想吃火锅，我答应了周末带她去。这件事她提过两次了。"
    assert real in tb._clean_memory_block(real, 2000), "真记忆一个字都不许丢"

    mixed = real + "\n\n```json\n" + '{"a":1}\n' * 30 + "```"
    out = tb._clean_memory_block(mixed, 2000)
    assert real in out and "{" not in out, "混着的时候留真的、擦掉数据"
