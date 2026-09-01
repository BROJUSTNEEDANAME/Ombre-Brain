"""TG 直连路径的冒烟测试。

存在的理由：py_compile 只查语法，抓不到「变量定义在使用之后」这类运行时错误。
真实事故：_trace 定义晚了 20 行 → 每一条消息都 UnboundLocalError，她那边看到的
是「这次回复没有生成出来」，而我以为只是慢。这个测试会真的把 _ask_claude 跑一遍。
"""
import asyncio
import sys
import types
import os

import pytest

os.environ.setdefault("LLM_API_KEY", "test")
os.environ.setdefault("TELEGRAM_API_BOT_TOKEN", "test")


def _stub_deps():
    """没装 openai / telegram 也要能真跑：塞最小替身进 sys.modules。
    跳过的测试等于没有测试——这个文件存在的意义就是真的执行一遍。"""
    if "openai" not in sys.modules:
        m = types.ModuleType("openai")

        class _C:
            def __init__(self, *a, **k):
                self.chat = types.SimpleNamespace(completions=self)

            async def create(self, **kw):
                raise AssertionError("测试里应当被替换掉")

        m.AsyncOpenAI = _C
        sys.modules["openai"] = m

    if "telegram" not in sys.modules:
        tg = types.ModuleType("telegram")
        tg.Update = type("Update", (), {"ALL_TYPES": []})
        tg.BotCommand = lambda *a, **k: None
        sys.modules["telegram"] = tg

        const = types.ModuleType("telegram.constants")
        const.ChatAction = types.SimpleNamespace(TYPING="typing", RECORD_VOICE="rv")
        sys.modules["telegram.constants"] = const

        ext = types.ModuleType("telegram.ext")
        for _n in ("Application", "ApplicationBuilder", "CommandHandler",
                   "MessageHandler"):
            setattr(ext, _n, type(_n, (), {}))
        ext.ContextTypes = type("ContextTypes", (), {"DEFAULT_TYPE": object})
        ext.filters = types.SimpleNamespace(PHOTO=1, VOICE=2, TEXT=4, COMMAND=8)
        sys.modules["telegram.ext"] = ext


def _load():
    _stub_deps()
    try:
        import telegram_bot  # noqa: PLC0415
    except ModuleNotFoundError as exc:      # telegram 等依赖缺失时跳过
        pytest.skip(f"依赖缺失，跳过：{exc}")
    return telegram_bot


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


class _Chunk:
    def __init__(self, delta):
        self.choices = [types.SimpleNamespace(delta=delta)]


async def _fake_stream(chunks):
    for c in chunks:
        yield _Chunk(_Delta(content=c))


def test_ask_claude_streams_without_runtime_errors(monkeypatch):
    """完整跑一遍直连：不许抛 NameError/UnboundLocalError，且分段发出。"""
    tb = _load()

    async def fake_create(**kw):
        assert kw.get("model"), "必须带 model"
        return _fake_stream(["醒了？\n\n", "先喝水 桌上那杯"])  # 无标点，会被兜底补上

    async def fake_brain(name, args):
        return "（假记忆）"

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)

    sent = []

    async def on_seg(s):
        sent.append(s)

    history = [{"role": "user", "content": "我回来了 今天好累"}]
    # 用 asyncio.run 而不是 get_event_loop：后者依赖全局循环，别的测试跑完
    # 把它关掉后这里会 RuntimeError，变成「单跑绿、全量红」的假故障。
    reply = asyncio.run(tb._ask_claude(history, on_segment=on_seg))

    assert sent == ["醒了？", "先喝水，桌上那杯。"], sent  # 默认补标点
    assert reply
    # /debug 依赖的记录必须齐全——「模型 None」就是这里缺失暴露出来的
    assert tb.LAST_TURN.get("model"), "LAST_TURN 缺 model，/debug 会显示 None"
    assert tb.LAST_TURN.get("trace"), "LAST_TURN 缺 trace，耗时明细会是空的"


def test_tiny_message_skips_memory_lookup(monkeypatch):
    """纯表情不该去翻记忆（翻了就是白等）。"""
    tb = _load()
    called = []

    async def fake_create(**kw):
        return _fake_stream(["嗯"])

    async def fake_brain(name, args):
        called.append(name)
        return "（假记忆）"

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)

    async def _noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "🥺"}], on_segment=_noop))
    assert called == [], f"表情消息不该调用记忆检索，实际调了 {called}"
    assert tb.LAST_TURN.get("tiny") is True


def test_memory_tag_never_reaches_her_and_is_saved_after_reply(monkeypatch):
    """记忆标签不许发给她；写记忆的工具不许出现在聊天工具表里。

    真实事故：他为了存一条 hold 写了 145.6 秒，正文 0 字，她干等两分半；
    被强制摘掉工具后又把没写完的记忆内容当成话说给她听。
    """
    tb = _load()

    async def fake_create(**kw):
        names = [t["function"]["name"] for t in (kw.get("tools") or [])]
        assert "hold" not in names and "grow" not in names, \
            f"聊天不该带写记忆的工具，实际带了 {names}"
        return _fake_stream(["先喝水 桌上那杯", "\n[memory:事实：她一天没吃饭]"])

    async def fake_brain(name, args):
        return "（假记忆）"

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)

    sent = []

    async def on_seg(s):
        sent.append(s)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "我一天没吃饭了"}],
                               on_segment=on_seg))

    joined = "".join(sent)
    assert "memory" not in joined.lower(), f"隐藏标签漏给她了：{sent}"
    assert "她一天没吃饭" not in joined, f"记忆内容漏给她了：{sent}"
    assert sent and sent[0].startswith("先喝水"), sent
    assert tb.LAST_TURN.get("memory_note") == "事实：她一天没吃饭", tb.LAST_TURN.get("memory_note")


def test_chat_tool_list_excludes_memory_writes():
    tb = _load()
    names = {t["function"]["name"] for t in tb.CHAT_TOOLS}
    assert not (names & {"hold", "grow", "trace"}), names
    assert "breath" in names and "make_page" in names, names


def test_image_is_transcribed_then_answered_on_fast_lane(monkeypatch):
    """图片先转述成文字，再走和文字消息完全相同的直连路径。

    真实事故：图片一直留在网页大脑那条线上，而那条线 60 秒超时，GLM-5.3 在
    上面动辄一两分钟 —— 每张图都必然「识图或回复失败」。
    """
    tb = _load()
    seen = {}

    async def fake_create(**kw):
        if kw.get("model") == tb.VISION_MODEL:
            seen["vision"] = True
            # 识图这一轮是普通（非流式）调用
            msg = types.SimpleNamespace(content="截图里写着：你已被移出群聊", tool_calls=None)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])
        seen["chat_prompt"] = kw["messages"][-1]["content"]
        return _fake_stream(["谁把你踢了"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))

    text = asyncio.run(tb._transcribe_image("ZmFrZQ=="))
    assert seen.get("vision"), "没有调用识图模型"
    assert "你已被移出群聊" in text, text


def _async_val(v):
    async def _c():
        return v
    return _c()


class _FakeBot:
    def __init__(self): self.sent = []
    async def send_chat_action(self, **kw): return None
    async def send_message(self, chat_id=None, text=None, **kw): self.sent.append(text)


class _FakeMsg:
    def __init__(self): self.replies = []
    async def reply_text(self, text, **kw): self.replies.append(text)


def test_direct_reply_end_to_end(monkeypatch):
    """真的把 _direct_reply 跑一遍。

    真实事故：把直连流程抽成模块级函数时，漏了它依赖的 _keep_typing —— 那个函数
    原本嵌在 on_message 里，搬出去就成了未定义名，每条消息一进去就 NameError，
    她发什么都没反应。上一版冒烟测试只测到 _ask_claude，正好漏过这一层。
    """
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream(["醒着呢", "\n\n你说"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    monkeypatch.setattr(tb, "_save_state", lambda: None)
    monkeypatch.setattr(tb, "_sync_main_line", lambda *a, **k: _async_val(None))

    bot, msg = _FakeBot(), _FakeMsg()
    update = types.SimpleNamespace(message=msg)
    context = types.SimpleNamespace(bot=bot)
    history = [{"role": "user", "content": "helloworld"}]

    asyncio.run(tb._direct_reply(update, context, 1, history, "mid:1", "helloworld"))

    assert bot.sent == ["醒着呢", "你说"], bot.sent
    assert not msg.replies, f"不该出现失败兜底：{msg.replies}"
    assert history[-1]["role"] == "assistant", history[-1]
    assert tb.LAST_TURN.get("first_bubble_s") is not None


def test_single_newline_also_splits_bubbles(monkeypatch):
    """单个换行也要分气泡。

    她的原话：「还是不分行，聚在一起看太累了」。他实际最常用单换行分句，
    而切分只认 ‖ 和空行，于是整段挤成一个大气泡。
    """
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream([
            "课表都敢背着我改，它才是旧的那个。\n",
            "没课更好。\n\n早饭照旧，铁剂随餐。‖睡回笼还是起来，你定。",
        ])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))

    sent = []

    async def on_seg(s):
        sent.append(s)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "今天没课"}], on_segment=on_seg))
    assert sent == [
        "课表都敢背着我改，它才是旧的那个。",
        "没课更好。",
        "早饭照旧，铁剂随餐。",
        "睡回笼还是起来，你定。",
    ], sent


def test_writing_mode_keeps_one_long_bubble(monkeypatch):
    """写文模式反过来：换行不许切，长正文保持整段。"""
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream(["第一段正文。\n第二段正文。\n\n第三段正文。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))

    sent = []

    async def on_seg(s):
        sent.append(s)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "写一段"}],
                               on_segment=on_seg, writing=True))
    assert len(sent) == 1, sent


def test_restore_punctuation_only_touches_unpunctuated_chinese():
    """他不打标点就替他补上——但别动本来就对的东西。

    她连着两次反馈「还没标点符号」：人设里写了规矩他也不照做，因为历史消息里
    全是他自己的无标点写法，那个示范比埋在几千字里的一句话有力。
    """
    tb = _load()
    f = tb.restore_punctuation
    assert f("哼什么 声音留给枕头 我这收账的今早不开门") == "哼什么，声音留给枕头，我这收账的今早不开门。"
    assert f("睡吧 醒来连本带利一起算") == "睡吧，醒来连本带利一起算。"
    # 本来就有标点 → 原样
    assert f("猫又炸毛了。") == "猫又炸毛了。"
    assert f("头还昏不昏？") == "头还昏不昏？"
    # 中英/数字之间的空格不许动
    assert f("girl 过来") == "girl 过来"
    assert f("铁剂 65mg 随餐") == "铁剂 65mg 随餐"
    # 没有空格就没什么好补的
    assert f("嗯") == "嗯"


def test_streamed_segments_get_punctuation(monkeypatch):
    """走到她手机上的那一条必须是补过标点的。"""
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream(["哼什么 声音留给枕头\n", "睡吧 醒来连本带利一起算"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))

    sent = []

    async def on_seg(s):
        sent.append(s)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "哼"}], on_segment=on_seg))
    assert sent == ["哼什么，声音留给枕头。", "睡吧，醒来连本带利一起算。"], sent


def test_writing_mode_punctuation_untouched(monkeypatch):
    """写文模式不许动他的正文。"""
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream(["白的 薄的 紧到能看见骨头"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))

    sent = []

    async def on_seg(s):
        sent.append(s)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "写"}],
                               on_segment=on_seg, writing=True))
    assert sent == ["白的 薄的 紧到能看见骨头"], sent


def test_liveness_rules_present_in_persona():
    """活人感四条必须真的在共用人设里（网页和 TG 同源）。"""
    from personality import CHAT_STYLE_SYSTEM as C
    assert "允许极短的一回合" in C
    assert "长度必须参差" in C
    assert "被戳到要真的破防" in C
    assert "禁止把一个梗系统化经营" in C


def test_memory_lookup_is_reused_within_a_burst(monkeypatch):
    """连着聊时复用记忆块，别每句都白等 3~5 秒；问到过去时必须现查。"""
    tb = _load()
    calls = []

    async def fake_create(**kw):
        return _fake_stream(["嗯"])

    async def fake_brain(name, args):
        calls.append(args.get("query", ""))
        return "（假记忆）"

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "我回来了"}], on_segment=noop))
    assert len(calls) == 1, calls                      # 第一句：现查
    asyncio.run(tb._ask_claude([{"role": "user", "content": "今天好累"}], on_segment=noop))
    assert len(calls) == 1, f"紧接着的一句应当复用，实际又查了：{calls}"
    asyncio.run(tb._ask_claude([{"role": "user", "content": "你还记得我上次说的吗"}],
                               on_segment=noop))
    assert len(calls) == 2, f"问到过去必须现查，实际没查：{calls}"


def test_model_override_is_used_and_reported(monkeypatch):
    """/model 换的型号必须真的用在请求上，并且 /debug 里报的是同一个。"""
    tb = _load()
    used = {}

    async def fake_create(**kw):
        used["model"] = kw.get("model")
        return _fake_stream(["嗯"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    tb.model_override.clear()
    asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}], on_segment=noop))
    assert used["model"] == tb.MODEL, used          # 没设过 → 用默认

    tb.model_override["model"] = "glm-5.2"
    tb._MEM_CACHE.clear()
    asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}], on_segment=noop))
    assert used["model"] == "glm-5.2", used         # 设了 → 用它
    assert "glm-5.2" in str(tb.LAST_TURN.get("model")), tb.LAST_TURN.get("model")
    tb.model_override.clear()


def test_model_choices_cover_thinking_combos(monkeypatch):
    """模型 × 思考的组合要真的作用到请求上；5.3 不提供「关思考」那档。"""
    tb = _load()
    names = [n for n, *_ in tb.MODEL_CHOICES]
    assert names == ["5.3", "5.2", "5.2t", "5.1", "5.1t"], names
    # 5.3 只有一档，且是「压思考」——它关不掉，交给档位协商降到 low
    assert [(m, off) for n, m, off, _ in tb.MODEL_CHOICES if m == "glm-5.3"] == [("glm-5.3", True)]

    seen = {}

    def fake_thinking(model, want_off):
        seen["want_off"] = want_off
        return {"thinking": {"type": "disabled"}} if want_off else None

    async def fake_create(**kw):
        seen["model"] = kw.get("model")
        return _fake_stream(["嗯"])

    monkeypatch.setattr(tb, "thinking_request", fake_thinking)
    monkeypatch.setattr(tb, "llm", types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=fake_create))))
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    tb.model_override.clear()
    tb.model_override.update({"model": "glm-5.2", "think_off": False})   # 5.2t
    asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}], on_segment=noop))
    assert seen["model"] == "glm-5.2" and seen["want_off"] is False, seen
    assert tb.current_choice_label() == "5.2t", tb.current_choice_label()

    tb.model_override.update({"model": "glm-5.2", "think_off": True})    # 5.2
    tb._MEM_CACHE.clear()
    asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}], on_segment=noop))
    assert seen["want_off"] is True, seen
    assert tb.current_choice_label() == "5.2", tb.current_choice_label()
    tb.model_override.clear()


def test_rapid_messages_are_merged_into_one_turn(monkeypatch):
    """她连发几条 → 攒着，等她停下来当成一整段回一次，而不是挨条各回。"""
    tb = _load()
    monkeypatch.setattr(tb, "BATCH_SECONDS", 0.05)
    seen = {}
    replies = []

    async def fake_direct(update, context, chat_id, history, mid, sync_text):
        seen["text"] = sync_text
        replies.append(sync_text)

    monkeypatch.setattr(tb, "_direct_reply", fake_direct)
    tb._pending_msgs.clear()
    tb._batch_tasks.clear()
    tb.histories.clear()

    upd = types.SimpleNamespace(message=types.SimpleNamespace(message_id=9))
    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        for t in ("修仙", "想吃烤鸡", "55"):
            await tb._queue_message(upd, ctx, 1, t)
            await asyncio.sleep(0.01)          # 打字间隔比等待窗口短
        await asyncio.sleep(0.2)               # 停下来

    asyncio.run(run())
    assert len(replies) == 1, f"应当只回一次，实际 {len(replies)} 次：{replies}"
    assert seen["text"] == "修仙\n想吃烤鸡\n55", seen
    assert tb.histories[1][-1]["content"] == "修仙\n想吃烤鸡\n55"


def test_slow_messages_are_not_merged(monkeypatch):
    """隔得久的两条不该被并进同一轮。"""
    tb = _load()
    monkeypatch.setattr(tb, "BATCH_SECONDS", 0.05)
    replies = []

    async def fake_direct(update, context, chat_id, history, mid, sync_text):
        replies.append(sync_text)

    monkeypatch.setattr(tb, "_direct_reply", fake_direct)
    tb._pending_msgs.clear()
    tb._batch_tasks.clear()
    tb.histories.clear()

    upd = types.SimpleNamespace(message=types.SimpleNamespace(message_id=9))
    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        await tb._queue_message(upd, ctx, 1, "在吗")
        await asyncio.sleep(0.2)
        await tb._queue_message(upd, ctx, 1, "睡了没")
        await asyncio.sleep(0.2)

    asyncio.run(run())
    assert replies == ["在吗", "睡了没"], replies
