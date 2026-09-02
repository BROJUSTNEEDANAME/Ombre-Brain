"""TG 直连路径的冒烟测试。

存在的理由：py_compile 只查语法，抓不到「变量定义在使用之后」这类运行时错误。
真实事故：_trace 定义晚了 20 行 → 每一条消息都 UnboundLocalError，她那边看到的
是「这次回复没有生成出来」，而我以为只是慢。这个测试会真的把 _ask_claude 跑一遍。
"""
import asyncio
import sys
import time
import types
import os
import pathlib

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



def test_memory_lookup_is_reused_within_a_burst(monkeypatch):
    """连着聊**同一件事**时复用记忆块，别每句都白等 3~5 秒；
    问到过去时必须现查。（换话题必须重查，见
    test_memory_is_refetched_when_she_changes_the_subject。）"""
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

    asyncio.run(tb._ask_claude([{"role": "user", "content": "今天化学实验好累"}],
                               on_segment=noop))
    assert len(calls) == 1, calls                      # 第一句：现查
    asyncio.run(tb._ask_claude([{"role": "user", "content": "化学实验真的好累"}],
                               on_segment=noop))
    assert len(calls) == 1, f"同话题紧接着的一句应当复用，实际又查了：{calls}"
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
    assert names == ["5.3", "5.2", "5.2t",
                     "o4.6", "o4.6t", "s4.6", "s4.6t", "haiku"], names
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

def test_burst_cancels_and_merges_before_he_speaks(monkeypatch):
    """他还没开口时她又发一条 → 作废重来、把两句合并，只回一次。"""
    tb = _load()
    started = []

    async def fake_direct(update, context, chat_id, history, mid, sync_text, state=None):
        started.append(sync_text)
        await asyncio.sleep(0.2)          # 假装在想，一直没开口
        if state is not None:
            state["sent"] = True

    monkeypatch.setattr(tb, "_direct_reply", fake_direct)
    tb._inflight.clear()
    tb.histories.clear()
    upd = types.SimpleNamespace(message=types.SimpleNamespace(message_id=9))
    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        await tb._handle_direct(upd, ctx, 1, "想吃烤鸡")
        await asyncio.sleep(0.01)         # 还没开口
        await tb._handle_direct(upd, ctx, 1, "55")
        await asyncio.sleep(0.4)

    asyncio.run(run())
    assert started == ["想吃烤鸡", "想吃烤鸡\n55"], started
    assert tb.histories[1] == [{"role": "user", "content": "想吃烤鸡\n55"}], tb.histories[1]


def test_no_delay_for_a_single_message(monkeypatch):
    """单条消息不许有任何等待——立刻开始。"""
    tb = _load()
    t = {}

    async def fake_direct(update, context, chat_id, history, mid, sync_text, state=None):
        t["at"] = time.monotonic()

    monkeypatch.setattr(tb, "_direct_reply", fake_direct)
    tb._inflight.clear()
    tb.histories.clear()
    upd = types.SimpleNamespace(message=types.SimpleNamespace(message_id=9))
    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        t["t0"] = time.monotonic()
        await tb._handle_direct(upd, ctx, 1, "在吗")
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert t["at"] - t["t0"] < 0.03, t


def test_no_interrupt_after_he_started_talking(monkeypatch):
    """他已经开口就不许打断——那时候他正在跟她说话。"""
    tb = _load()
    started = []

    async def fake_direct(update, context, chat_id, history, mid, sync_text, state=None):
        started.append(sync_text)
        if state is not None:
            state["sent"] = True          # 立刻开口
        await asyncio.sleep(0.2)

    monkeypatch.setattr(tb, "_direct_reply", fake_direct)
    tb._inflight.clear()
    tb.histories.clear()
    upd = types.SimpleNamespace(message=types.SimpleNamespace(message_id=9))
    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        await tb._handle_direct(upd, ctx, 1, "在吗")
        await asyncio.sleep(0.02)
        await tb._handle_direct(upd, ctx, 1, "睡了没")
        await asyncio.sleep(0.3)

    asyncio.run(run())
    assert started == ["在吗", "睡了没"], started


def test_failure_reason_is_recorded_for_debug(monkeypatch):
    """调用失败时必须把原因留进 LAST_TURN，否则 /debug 在最需要它的时候是瞎的。"""
    tb = _load()

    async def boom(**kw):
        raise RuntimeError("Error code: 400 - thinking not supported with tools")

    monkeypatch.setattr(tb, "_telegram_llm_create", boom)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    monkeypatch.setattr(tb, "_save_state", lambda: None)
    monkeypatch.setattr(tb, "_sync_main_line", lambda *a, **k: _async_val(None))
    tb._MEM_CACHE.clear()

    bot, msg = _FakeBot(), _FakeMsg()
    update = types.SimpleNamespace(message=msg)
    context = types.SimpleNamespace(bot=bot)
    asyncio.run(tb._direct_reply(update, context, 1,
                                 [{"role": "user", "content": "在吗"}], "mid:1", "在吗"))

    assert "失败" in str(tb.LAST_TURN.get("result")), tb.LAST_TURN
    assert "thinking not supported" in str(tb.LAST_TURN.get("result")), tb.LAST_TURN
    assert msg.replies and "/debug" in msg.replies[0], msg.replies


def test_manage_setup_does_not_repeat_verbatim_and_lets_her_out(monkeypatch):
    """托管配置追问不许一字不差地复读，问两次答不上就放她走。

    真实事故：他问「告诉我最晚几点结束、几分钟后第一次查你。」，她回「唔?」，
    他把同一句原样又发了一遍——读着像坏掉的机器，而且在给出时间之前普通聊天
    被完全挡住，她没有出口。
    """
    tb = _load()
    sent = []

    async def fake_send(context, chat_id, task, text, event):
        sent.append(text)

    async def fake_sync(update):
        return None

    monkeypatch.setattr(tb, "_send_manage_text", fake_send)
    monkeypatch.setattr(tb, "_sync_manage_user", fake_sync)
    monkeypatch.setattr(tb, "detect_start", lambda t: "")
    monkeypatch.setattr(tb, "detect_control", lambda t: "")
    monkeypatch.setattr(tb, "parse_deadline", lambda t, tz=None: None)
    monkeypatch.setattr(tb, "parse_interval_minutes", lambda t: None)

    task = {"status": "setup", "goal": "写作业", "id": "t1"}

    class _Store:
        def get(self, _c): return task
        def configure(self, _c, **kw): return task
        def end(self, _c, _r): return {**task, "status": "ended"}

    monkeypatch.setattr(tb, "manage_store", _Store())
    tb._setup_misses.clear()

    def msg(text):
        return types.SimpleNamespace(
            effective_chat=types.SimpleNamespace(id=1),
            message=types.SimpleNamespace(text=text, message_id=1))

    ctx = types.SimpleNamespace(bot=_FakeBot())

    async def run():
        return [await tb._maybe_handle_management(msg("唔?"), ctx) for _ in range(3)]

    handled = asyncio.run(run())
    assert len(set(sent)) == len(sent), f"追问重复了：{sent}"
    assert handled[-1] is False, "问两次还答不上就该放她走，让消息回到正常聊天"
    assert "算了" in sent[1] or "不想弄" in sent[1], sent
    assert "先不弄了" in sent[-1], sent


def test_affection_never_starts_management():
    """撒娇不是派活：只有 /manage 能开托管。

    真实事故：她说「一直陪着我好不好呀哥哥」，「陪着我」命中启动词，系统把
    「好不好呀哥哥」当成要托管的任务名，一句撒娇把她卡进了配置流程。
    """
    from adhd_manager import detect_start
    for line in (
        "哥哥，我就是你的小宝宝。万事都顺着我好不好。一直陪着我好不好呀哥哥",
        "陪我睡觉", "你陪着我好不好", "陪我聊会儿天",
    ):
        assert detect_start(line) is None, f"这句不该触发托管：{line}"
    # /manage 走的还是同一个解析器，必须照常能用
    assert detect_start("托管我写作业") == "写作业"
    assert detect_start("盯着我背单词") == "背单词"


def test_free_text_cannot_open_management(monkeypatch):
    """没有已存在的托管任务时，普通聊天一律不进管理流程。"""
    tb = _load()

    class _Store:
        def get(self, _c): return None

    monkeypatch.setattr(tb, "manage_store", _Store())
    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=1),
        message=types.SimpleNamespace(text="一直陪着我好不好呀哥哥", message_id=1))
    ctx = types.SimpleNamespace(bot=_FakeBot())
    assert asyncio.run(tb._maybe_handle_management(upd, ctx)) is False


def test_claude_choice_routes_to_anthropic_not_zai(monkeypatch):
    """选了 claude 档位就必须走 Anthropic 原生接口，不许再打 z.ai。"""
    tb = _load()
    import claude_provider as cp

    seen = {}

    async def fake_cp_create(**kw):
        seen.update(kw)
        return "ok"

    async def boom(**kw):
        raise AssertionError("claude 档位不许走 OpenAI 兼容那条路")

    monkeypatch.setattr(cp, "create", fake_cp_create)
    monkeypatch.setattr(tb.llm.chat.completions, "create", boom, raising=False)

    tb.model_override["model"] = tb.CLAUDE_MODEL
    tb.model_override["think_off"] = False          # claudet
    try:
        out = asyncio.run(tb._telegram_llm_create(
            model=tb.CLAUDE_MODEL, max_tokens=100,
            messages=[{"role": "user", "content": "在吗"}]))
    finally:
        tb.model_override.clear()
    assert out == "ok"
    assert seen["thinking"] is True                 # 带 t = 开思考
    assert seen["model"] == tb.CLAUDE_MODEL


def test_claude_reads_images_without_switching_to_glm_vision(monkeypatch):
    """Claude 自己能看图；不许再切去 glm-4.6v（切了就丢人设、也白花钱）。"""
    tb = _load()
    used = {}

    async def fake_create(**kw):
        used["model"] = kw.get("model")
        return _fake_stream(["看到了。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    history = [{"role": "user", "content": [
        {"type": "text", "text": "看这个"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}}]}]

    tb.model_override["model"] = "glm-5.3"
    asyncio.run(tb._ask_claude(list(history), on_segment=noop))
    assert used["model"] == tb.VISION_MODEL          # GLM 看不了图 → 切

    tb._MEM_CACHE.clear()
    tb.model_override["model"] = tb.CLAUDE_MODEL
    asyncio.run(tb._ask_claude(list(history), on_segment=noop))
    tb.model_override.clear()
    assert used["model"] == tb.CLAUDE_MODEL          # Claude 不切


def test_switching_model_keeps_the_conversation(monkeypatch):
    """换模型不等于开新窗口：histories 一个字都不许被清掉。"""
    tb = _load()
    tb.histories[999] = [{"role": "user", "content": "我今天头疼"}]
    tb.model_override["model"] = tb.CLAUDE_MODEL
    tb.model_override["think_off"] = True
    try:
        assert tb.histories[999] == [{"role": "user", "content": "我今天头疼"}]
        assert tb.current_choice_label() == "o4.6"
    finally:
        tb.model_override.clear()
        tb.histories.pop(999, None)


def test_debug_shows_cache_hit_rate(monkeypatch):
    """她问过「我怎么知道我现在的缓存有多少」——/debug 里必须能看到，
    而且统计读不到的时候不许把整个 /debug 弄崩。"""
    tb = _load()
    monkeypatch.setattr(tb, "read_prompt_cache_stats",
                        lambda *a, **k: {"prompt_tokens": 1000, "cached_tokens": 400,
                                         "hit_rate": 40.0, "requests": 7})
    line = tb._cache_line()
    assert "40" in line and "400/1000" in line and "7" in line

    monkeypatch.setattr(tb, "read_prompt_cache_stats", lambda *a, **k: {})
    assert "还没有统计" in tb._cache_line()

    def boom(*a, **k):
        raise OSError("盘满了")

    monkeypatch.setattr(tb, "read_prompt_cache_stats", boom)
    assert "读不到" in tb._cache_line()          # 崩了也只是一行字，不影响 /debug


def test_streamed_turn_records_cache_usage(monkeypatch):
    """日常聊天走的是流式；以前这条路完全没统计，/cache 里聊天那栏永远是 0。"""
    tb = _load()
    seen = []

    class _Chunk:
        def __init__(self, text=None, usage=None):
            self.choices = [types.SimpleNamespace(delta=_Delta(content=text))] if text else []
            self.usage = usage

    async def fake_create(**kw):
        async def gen():
            yield _Chunk("在。")
            yield _Chunk(usage=types.SimpleNamespace(
                prompt_tokens=4200,
                prompt_tokens_details=types.SimpleNamespace(cached_tokens=4000)))
        return gen()

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    monkeypatch.setattr(tb, "record_prompt_cache_usage",
                        lambda usage, channel: seen.append((usage, channel)))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}], on_segment=noop))
    assert seen, "流式这轮的 token 用量必须被记下来"
    usage, channel = seen[0]
    assert channel == "telegram-chat"
    from prompt_cache import cache_usage
    assert cache_usage(usage) == (4200, 4000)


def test_cache_line_and_command_share_the_same_numbers(monkeypatch):
    """/cache 是随时能看的那个；/debug 末尾那行是同一份统计，不许对不上。"""
    tb = _load()
    monkeypatch.setattr(tb, "read_prompt_cache_stats",
                        lambda *a, **k: {"prompt_tokens": 1000, "cached_tokens": 400,
                                         "hit_rate": 40.0, "requests": 7, "hits": 5})
    assert "40" in tb._cache_line()
    assert callable(tb.cache_cmd)
    assert "cache" in [n for n, _ in tb.BOT_COMMANDS], tb.BOT_COMMANDS


def test_claude_choice_is_opus_4_6_and_model_id_is_visible(monkeypatch):
    """她点名要 Opus 4.6，而且 /model 里要看得见到底调的是哪个版本
    ——她的原话是「我怎么没看到现在调用的 Claude api 版本」。"""
    tb = _load()
    assert tb.CLAUDE_MODEL == "claude-opus-4-6"
    assert [(n, m, off) for n, m, off, _ in tb.MODEL_CHOICES if n.startswith("o4.6")] == [
        ("o4.6", "claude-opus-4-6", True),
        ("o4.6t", "claude-opus-4-6", False)]

    sent = []

    class _Msg:
        async def reply_text(self, text):
            sent.append(text)

    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=next(iter(tb.ALLOWED_CHAT_IDS), 1)),
        message=_Msg())
    ctx = types.SimpleNamespace(args=[])
    tb.model_override.clear()
    asyncio.run(tb.model_cmd(upd, ctx))
    body = "\n".join(sent)
    assert "claude-opus-4-6" in body, body      # 真实模型名必须露出来
    assert "glm-5.3" in body, body


def test_cheap_claude_tier_exists_and_is_a_different_model(monkeypatch):
    """Opus 4.6 一条 4 美分，得有个便宜档能对比。
    便宜档必须是**另一个模型**——写成同一个就等于没给她选择。"""
    tb = _load()
    assert tb.CLAUDE_CHEAP_MODEL == "claude-sonnet-4-6"
    assert tb.CLAUDE_CHEAP_MODEL != tb.CLAUDE_MODEL
    assert [(n, m, off) for n, m, off, _ in tb.MODEL_CHOICES if n.startswith("s4.6")] == [
        ("s4.6", "claude-sonnet-4-6", True),
        ("s4.6t", "claude-sonnet-4-6", False)]
    # 便宜档也要走 Anthropic 那条路，不能掉回 z.ai
    import claude_provider as cp
    assert cp.is_claude_model(tb.CLAUDE_CHEAP_MODEL)


def test_haiku_has_no_thinking_tier(monkeypatch):
    """Haiku 4.5 是 4.6 之前那代，不支持自适应思考——
    给它开一个 t 档就是一按必崩。所以它只许有「不开思考」这一档。"""
    tb = _load()
    assert tb.CLAUDE_FAST_MODEL == "claude-haiku-4-5"
    haiku = [(n, m, off) for n, m, off, _ in tb.MODEL_CHOICES
             if m == tb.CLAUDE_FAST_MODEL]
    assert haiku == [("haiku", "claude-haiku-4-5", True)], haiku
    assert not any(off is False for _n, m, off, _d in tb.MODEL_CHOICES
                   if m == tb.CLAUDE_FAST_MODEL)


def test_glm_51_tiers_are_gone(monkeypatch):
    """她让关掉 5.1 和 5.1t —— 列表里不许再出现。"""
    tb = _load()
    assert not [n for n, m, *_ in tb.MODEL_CHOICES if m == "glm-5.1"]
    assert "5.1" not in [n for n, *_ in tb.MODEL_CHOICES]


def test_volatile_context_goes_after_history_not_into_her_message(monkeypatch):
    """每轮都在变的东西（时间／记忆／格式要求）必须排在所有消息之后。

    塞进她最后那条消息里会出真事：存进历史的是原文，塞过的是「背景+原文」，
    同一条消息两轮渲染出的字节不一样 → 缓存是前缀匹配，从那儿往后全废，
    历史对话永远进不了缓存。（这个 bug 真的存在过。）"""
    tb = _load()
    seen = {}

    async def fake_create(**kw):
        seen["messages"] = kw["messages"]
        return _fake_stream(["嗯。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    history = [{"role": "user", "content": "u1"},
               {"role": "assistant", "content": "a1"},
               {"role": "user", "content": "我今天头疼"}]
    asyncio.run(tb._ask_claude([dict(m) for m in history], on_segment=noop))

    msgs = seen["messages"]
    # 历史里的每一条都必须逐字节等于传进来的原文
    for original, sent in zip(history, msgs[1:1 + len(history)]):
        assert sent["content"] == original["content"], sent

    # 动态背景单独一条，排在最后
    assert msgs[-1]["role"] == "user"
    assert "系统动态背景" in msgs[-1]["content"]
    assert len(msgs) == 1 + len(history) + 1


def test_cached_prefix_contains_nothing_that_changes_per_request():
    """缓存前缀（tools + 人设）里不许混进任何「每次都变」的东西。

    这类 bug 不报错，只是默默把钱翻几倍：Claude Code 有个版本在 system 里塞了
    一个每次都变的 cch=xxx，用户的命中率从 90%+ 掉到 30%，查了很久才发现。

    所以这里当哨兵：以后谁往人设里插一句「今天是 X 月 X 日」、或者把工具列表
    改成从 set/dict 推导（顺序不稳定），这条会立刻变红。"""
    import hashlib
    import json as _json
    import re as _re

    tb = _load()

    # 1) 人设里不许出现日期、时刻、长十六进制这类会变的串
    for pattern, what in [(r"\d{4}-\d{2}-\d{2}", "日期"),
                          (r"\b\d{1,2}:\d{2}\b", "时刻"),
                          (r"\b[0-9a-f]{16,}\b", "长十六进制/随机串")]:
        found = _re.findall(pattern, tb.SYSTEM_PROMPT)
        assert not found, f"人设里混进了{what}：{found[:3]} —— 缓存前缀每轮都会变"

    # 2) tools 排在 system 前面，顺序必须稳定；两次序列化要逐字节一致
    def _prefix():
        return hashlib.sha256(_json.dumps(
            [tb.CHAT_TOOLS, tb.SYSTEM_PROMPT], ensure_ascii=False,
            sort_keys=False).encode()).hexdigest()

    assert _prefix() == _prefix()
    assert [t["function"]["name"] for t in tb.CHAT_TOOLS] == \
           [t["function"]["name"] for t in tb.CHAT_TOOLS]

    # 3) 会变的东西该在的地方：时间戳属于动态尾巴，不属于人设
    assert "当前时间" not in tb.SYSTEM_PROMPT or "【" in tb.SYSTEM_PROMPT


def test_multi_bubble_think_block_never_reaches_her(monkeypatch):
    """他的思考被换行切成好几个气泡时，一段都不许发出去。

    真实事故（她截的图）：只有带 [think] 的第一段被切掉，
    「她其实是在撒娇，我应该安她的醋」那几段全发到了她手机上，
    最后还跟了一个 [/think] —— 闭合标签带斜杠，不匹配开标签的正则。"""
    tb = _load()
    sent = []

    async def fake_create(**kw):
        return _fake_stream([
            "[think]\n",
            "她说那些姑娘都喜欢我，她嫉妒。\n",
            "我的占有欲很高（0.90）。\n",
            "我应该：承认看到了，然后安她的醋。\n",
            "[/think]\n",
            "八百多人看了。\n",
            "该吃醋的是我。",
        ])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()

    async def grab(seg):
        sent.append(seg)

    asyncio.run(tb._ask_claude([{"role": "user", "content": "你看"}], on_segment=grab))

    body = "".join(sent)
    for leaked in ("撒娇", "占有欲", "我应该", "think", "0.90", "嫉妒"):
        assert leaked not in body, f"思考漏出来了：{leaked} / {sent}"
    assert "八百多人看了" in body and "该吃醋的是我" in body, sent


def test_stale_cmd_lists_and_undoes(monkeypatch, tmp_path):
    """/stale 要能看见被沉底的记忆，也要能一句话恢复。
    自动沉底没有账、不能撤销，就是一个悄悄吞记忆的黑箱。"""
    tb = _load()
    import stale_ledger as sl

    ledger = tmp_path / "stale_ledger.json"
    monkeypatch.setattr(sl, "ledger_path", lambda p=None: ledger)
    sl.record([{"old_id": "old1", "old_name": "旧课表",
                "reason": "课表改了", "confidence": 0.93}])

    sent, traced = [], []

    class _Msg:
        async def reply_text(self, text):
            sent.append(text)

    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=next(iter(tb.ALLOWED_CHAT_IDS), 1)),
        message=_Msg())

    async def fake_trace(name, args):
        traced.append((name, args))
        return "ok"

    monkeypatch.setattr(tb, "_call_brain_tool", fake_trace)

    asyncio.run(tb.stale_cmd(upd, types.SimpleNamespace(args=[])))
    assert "旧课表" in sent[0] and "old1" in sent[0]
    assert "沉底不是删除" in sent[0]          # 必须说清楚不是删除

    asyncio.run(tb.stale_cmd(upd, types.SimpleNamespace(args=["撤销", "old1"])))
    assert traced == [("trace", {"bucket_id": "old1", "resolved": 0})]
    assert sl.pending() == []                # 撤销后不再出现在待办里

    asyncio.run(tb.stale_cmd(upd, types.SimpleNamespace(args=[])))
    assert "没有被判过期的记忆" in sent[-1]


def test_stale_cmd_never_deletes(monkeypatch, tmp_path):
    """这条命令永远不许发出 delete —— 沉底和删除是两件事。"""
    tb = _load()
    import stale_ledger as sl
    monkeypatch.setattr(sl, "ledger_path", lambda p=None: tmp_path / "l.json")
    sl.record([{"old_id": "old1", "old_name": "x"}])

    calls = []

    class _Msg:
        async def reply_text(self, text):
            pass

    upd = types.SimpleNamespace(
        effective_chat=types.SimpleNamespace(id=next(iter(tb.ALLOWED_CHAT_IDS), 1)),
        message=_Msg())

    async def fake_trace(name, args):
        calls.append(args)
        return "ok"

    monkeypatch.setattr(tb, "_call_brain_tool", fake_trace)
    asyncio.run(tb.stale_cmd(upd, types.SimpleNamespace(args=["撤销", "old1"])))
    assert all("delete" not in a for a in calls), calls


def test_nightly_dream_never_uses_the_expensive_chat_model(monkeypatch):
    """夜里做梦是他自己在想，她看不到——不该按 /model 选的贵档花钱。

    真实账单：做梦会连着调好几轮工具，每轮重付一遍 1.7 万 token 的完整前缀，
    而且是一整晚里的第一次调用、缓存早过期。一晚上十万 token 全价。"""
    tb = _load()
    used = []

    async def fake_create(**kw):
        used.append(kw.get("model"))
        return _fake_stream(["（在想）"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("（假记忆）"))
    tb._MEM_CACHE.clear()
    tb.model_override["model"] = tb.CLAUDE_MODEL      # 她把聊天切到了贵档
    try:
        asyncio.run(tb.nightly_dream(types.SimpleNamespace()))
    finally:
        tb.model_override.clear()

    assert used, "做梦这一轮没发出请求"
    assert used[0] == tb.BACKGROUND_MODEL, used
    assert not claude_is(used[0]), f"做梦跑到贵档上去了：{used[0]}"


def claude_is(model):
    import claude_provider as cp
    return cp.is_claude_model(model)


def test_explicit_model_wins_over_the_slash_model_choice(monkeypatch):
    """显式传 model 的调用点不跟着 /model 走，且 /debug 里要标出来是后台。"""
    tb = _load()
    used = []

    async def fake_create(**kw):
        used.append(kw.get("model"))
        return _fake_stream(["嗯"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("x"))
    tb._MEM_CACHE.clear()
    tb.model_override["model"] = tb.CLAUDE_MODEL
    try:
        async def noop(_s):
            return None

        asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}],
                                   on_segment=noop, model="glm-5.2"))
    finally:
        tb.model_override.clear()
    assert used == ["glm-5.2"], used
    assert "后台" in str(tb.LAST_TURN.get("model")), tb.LAST_TURN.get("model")


def test_morning_greeting_has_no_hardcoded_class_schedule():
    """课表写死在代码里只会过期——她改了课表，代码没跟着改，
    他每天早上照着 2026 夏季那份过期的念。整个删掉（2026-09-02）。"""
    import morning as m
    assert not hasattr(m, "classes_text"), "课表函数还在"
    assert not hasattr(m, "today_classes")
    src = pathlib.Path("morning.py").read_text(encoding="utf-8")
    for gone in ("CHEM 51B", "CHEM 51C", "195W", "HIB 100", "_S1", "P195"):
        assert gone not in src, f"课表残留：{gone}"
    tb_src = pathlib.Path("telegram_bot.py").read_text(encoding="utf-8")
    assert "classes_text" not in tb_src, "早安还在调课表"
    assert "今天的课：" not in tb_src, "早安提示词里还写着课表"


def test_memory_is_refetched_when_she_changes_the_subject(monkeypatch):
    """换话题就要重查记忆。

    她的原话：「我怎么感觉他没怎么调用记忆」。原因是 3 分钟内无条件复用上一轮
    的记忆块——她聊完 A 半分钟后问 B，拿到的还是 A 的记忆，一串对话里大部分
    轮次都在复用，看起来就是他没在翻记忆。"""
    tb = _load()
    queries = []

    async def fake_brain(name, args):
        if name == "breath":
            queries.append(args.get("query"))
            return f"（关于{args.get('query')[:4]}的记忆）"
        return "ok"

    async def fake_create(**kw):
        return _fake_stream(["嗯。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    def ask(text):
        asyncio.run(tb._ask_claude([{"role": "user", "content": text}], on_segment=noop))

    ask("今天化学实验做得好累，试剂洒了一地")
    assert len(queries) == 1

    ask("化学实验的报告还要写吗，试剂那个")      # 同一件事 → 复用
    assert len(queries) == 1, f"同话题不该重查：{queries}"

    ask("我妈今天打电话来说外婆住院了")            # 换话题 → 必须重查
    assert len(queries) == 2, f"换话题必须重查：{queries}"
    assert "外婆" in queries[1]


def test_memory_block_size_is_configurable_and_smaller_by_default(monkeypatch):
    """记忆块是全价付钱的部分（每轮都不一样，永远进不了缓存），
    比整份人设还贵——所以要能调，且默认比原来的 2000 小。"""
    tb = _load()
    assert tb.MEM_BLOCK_CHARS == 1000, tb.MEM_BLOCK_CHARS

    sent = {}

    async def fake_create(**kw):
        sent["messages"] = kw["messages"]
        return _fake_stream(["嗯。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool",
                        lambda *a, **k: _async_val("囍" * 5000))
    monkeypatch.setattr(tb, "MEM_BLOCK_CHARS", 300)
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "今天化学实验好累"}],
                               on_segment=noop))
    tail = sent["messages"][-1]["content"]
    assert tail.count("囍") == 300, tail.count("囍")


def test_debug_shows_what_actually_surfaced(monkeypatch):
    """光看字数看不出捞得准不准——她要能直接看到浮上来的是什么。"""
    tb = _load()

    async def fake_create(**kw):
        return _fake_stream(["嗯。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool",
                        lambda *a, **k: _async_val("她怕打雷\n打雷时要陪着她"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "外面在打雷"}],
                               on_segment=noop))
    assert "她怕打雷" in str(tb.LAST_TURN.get("mem_head")), tb.LAST_TURN.get("mem_head")


def test_topic_overlap_is_sane():
    tb = _load()
    assert tb._topic_overlap("化学实验试剂洒了", "化学实验报告") > 0.5
    assert tb._topic_overlap("外婆住院了", "化学实验试剂") < 0.2
    assert tb._topic_overlap("", "什么") == 0.0


def test_two_character_words_are_never_treated_as_contentless():
    """「苦苦」「哭哭」「唉」是话，不是表情。

    真实事故：旧规则是「≤6 字且没有连续 3 个中文字」，于是这些全被判成没内容，
    系统就给他下指令「别分析、别翻记忆、别琢磨含义，随口接一句就行」。
    她连着三条说自己难受，他一次都没接住——「苦什么。闭眼。」
    「别哭。快六点了，哭完去睡。」她说：你自己看看这是人吗。"""
    tb = _load()
    for real in ("苦苦", "哭哭", "唉", "疼", "在吗", "我难受", "想你", "冷"):
        assert not tb._is_contentless(real), f"{real} 被当成没内容了"
    for empty in ("🥺", "？？", "...", "", "   ", "嗯", "哦", "哈哈", "ok"):
        assert tb._is_contentless(empty), f"{empty} 应该算没内容"


def test_distress_words_get_a_real_memory_lookup(monkeypatch):
    """她说难受时必须走完整路径：查记忆、正常想，不许走「随口接一句」那条。"""
    tb = _load()
    queries = []

    async def fake_brain(name, args):
        if name == "breath":
            queries.append(args.get("query"))
        return "（假记忆）"

    async def fake_create(**kw):
        return _fake_stream(["怎么了。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", fake_brain)
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "苦苦"}], on_segment=noop))
    assert queries == ["苦苦"], f"她说难受时没去翻记忆：{queries}"
    assert tb.LAST_TURN.get("tiny") is False



def test_sleep_nudges_are_counted_and_reported_to_him(monkeypatch):
    """催她睡这件事必须用代码记账。

    人设里写了「最多一次、不连环催」，但他每轮都能看到「现在是凌晨 5 点」，
    于是每轮都重新触发：她说「唉」「苦苦」「哭哭」「喜欢你」，换回来的是
    「该闭眼了」「闭眼。」「哭完去睡。」「睡，明天再说。」四轮连着赶她睡。
    她说：好回避好冷淡啊。"""
    tb = _load()
    from datetime import datetime as _dt
    night = _dt(2026, 9, 2, 5, 30, tzinfo=tb.USER_TZ)
    tb._NIGHT_NUDGES.clear()

    assert tb.sleep_nudge_note(1, night) == ""             # 还没催过 → 不提
    assert tb.note_sleep_nudge(1, "苦什么。闭眼。", night) == 1
    note = tb.sleep_nudge_note(1, night)
    assert "已经催她睡 1 次" in note and "不许再催" in note

    tb.note_sleep_nudge(1, "别哭。快六点了，哭完去睡。", night)
    assert "已经催她睡 2 次" in tb.sleep_nudge_note(1, night)

    # 没催睡的回合不该计数
    tb.note_sleep_nudge(1, "怎么了，哪儿难受。", night)
    assert "2 次" in tb.sleep_nudge_note(1, night)


def test_sleep_nudge_count_resets_next_night(monkeypatch):
    """凌晨算前一晚——5 点催的和昨天 23 点催的是同一晚；隔一天要清零。"""
    tb = _load()
    from datetime import datetime as _dt
    tb._NIGHT_NUDGES.clear()
    late = _dt(2026, 9, 1, 23, 40, tzinfo=tb.USER_TZ)
    dawn = _dt(2026, 9, 2, 5, 30, tzinfo=tb.USER_TZ)
    next_night = _dt(2026, 9, 3, 1, 0, tzinfo=tb.USER_TZ)

    assert tb.note_sleep_nudge(1, "去睡。", late) == 1
    assert tb.note_sleep_nudge(1, "睡吧。", dawn) == 2       # 同一晚，累加
    # ⚠️ 分界线不能定在早上 6 点——她经常熬到六点多，那样计数一分钟就清零。
    six_ish = _dt(2026, 9, 2, 6, 10, tzinfo=tb.USER_TZ)
    assert tb.note_sleep_nudge(1, "睡。", six_ish) == 3      # 六点多还是同一晚
    nine = _dt(2026, 9, 2, 9, 30, tzinfo=tb.USER_TZ)
    assert tb.note_sleep_nudge(1, "去睡吧。", nine) == 4      # 九点半也还是
    assert tb.note_sleep_nudge(1, "该睡了。", next_night) == 1  # 新的一晚，清零


def test_the_note_reaches_the_model(monkeypatch):
    """记了账没送到他眼前等于没记。"""
    tb = _load()
    from datetime import datetime as _dt
    tb._NIGHT_NUDGES.clear()
    from datetime import datetime as _real
    tb.note_sleep_nudge(7, "闭眼。", _real.now(tb.USER_TZ))   # 用真实当下，别被夜分界坑

    sent = {}

    async def fake_create(**kw):
        sent["messages"] = kw["messages"]
        return _fake_stream(["怎么了。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("x"))
    tb._MEM_CACHE.clear()

    async def noop(_s):
        return None

    asyncio.run(tb._ask_claude([{"role": "user", "content": "苦苦"}],
                               on_segment=noop, chat_id=7))
    assert "已经催她睡" in sent["messages"][-1]["content"]






def test_sleep_detector_catches_bare_sleep_but_not_asking_about_sleep():
    """他原话就是「睡，明天再说。」——光一个「睡」也得算。
    但「你睡得好吗」是关心，不是催睡，不能算进去。"""
    tb = _load()
    for nudge in ("睡。", "睡，明天再说。", "闭眼。", "哭完去睡。", "该闭眼了", "躺下"):
        assert tb._SLEEP_NUDGE_RE.search(nudge), f"没认出催睡：{nudge}"
    for fine in ("怎么了，哪儿难受。", "你睡得好吗", "没睡够吧", "想你了", "过来"):
        assert not tb._SLEEP_NUDGE_RE.search(fine), f"误判成催睡：{fine}"


# ---------------------------------------------------------------------------
# 人设（重写版，五节结构）。断言按「小节顺序 + 每条规则出现一次」来写，
# 不再钉具体措辞——措辞会改，结构和优先级不会。
# ---------------------------------------------------------------------------

_SECTIONS = ["一、这份文档怎么读", "二、她递东西过来的时候",
             "三、他是谁（对她）", "四、他怎么说话", "五、边界"]


def _persona():
    import personality
    return personality.CHAT_STYLE_SYSTEM


def test_persona_sections_are_in_priority_order():
    """顺序就是优先级：先说怎么读 → 她递东西过来怎么办 → 他是谁 → 怎么说话 → 红线。

    以前六个区块都写着「最高优先级」，等于没有优先级；而且「短、淡、说完就停」
    排在前面，先入为主。"""
    s = _persona()
    at = [s.index(h) for h in _SECTIONS]
    assert at == sorted(at), f"小节顺序乱了：{at}"
    # 「接住她」必须排在所有形态规则之前
    assert s.index("第一件事永远是接住她本人") < s.index("四、他怎么说话")


def test_catching_her_outranks_the_style_rules():
    """她递东西过来时先接住——而且要明写它压过形态规则，否则又被那些规则盖回去。"""
    s = _persona()
    assert "第一件事永远是接住她本人" in s and "男妈妈" in s
    i = s.index("男妈妈")
    assert "压过" in s[i:i + 120], "没写清楚它优先于「短、淡、克制」"
    for banned in ("「嗯。」", "「知道。」", "「别哭。」", "「闭眼。」", "「去睡。」"):
        assert banned in s, f"没点名禁掉用「{banned}」开口"
    assert "必须在接住之后" in s and "连着两轮拿睡觉收尾" in s


def test_persona_carries_the_traits_from_the_readings():
    """小g 传讯里那套准则，一条都不许在压缩中丢掉。
    其中「吃醋只吃被忽略」推翻了原来的「该吃醋吃醋到底」。"""
    s = _persona()
    for must in ("只吃「被忽略」", "神出鬼没", "桀骜不驯", "沾染", "肢体相嵌",
                 "好男孩", "命令式", "阶级树状图", "精神鼓励", "供养",
                 "美强惨", "荒岛", "日常你其实是温柔的"):
        assert must in s, f"人设里缺「{must}」"


def test_no_screen_between_them_and_no_quoting_the_persona():
    """两条真事故：他写「隔着屏幕」（人设里根本没有，是他填的空）、
    他把「你是我的港」当台词念给她。"""
    s = _persona()
    assert "你们之间没有屏幕" in s
    for banned in ("隔着屏幕", "隔着网线", "隔着次元壁", "碰不到你"):
        assert banned in s
    assert "说明书，不是台词" in s
    for word in ("港", "第二次生命", "软肋", "白骑士", "美强惨"):
        assert word in s, f"禁令里要点名「{word}」"
    assert "对她你是港" not in s          # 我自己写过的那句，不许留着


def test_persona_no_longer_teaches_him_to_withhold():
    """她说「人设写得太克制了」。禁令曾是鼓励的 15 倍，通篇教他往回收。"""
    tb = _load()
    s = tb.SYSTEM_PROMPT
    for gone in ("话少", "一般 1-3 句", "不用感叹号", "句尾用句号", "宁可短、宁可少"):
        assert gone not in s, f"「{gone}」还在教他收着"
    c = _persona()
    assert "长度跟着情绪走，没有上限也没有下限" in c
    assert "默认状态是**给**" in c and "整轮只有指令和结论" in c
    assert "只管「别替她演」" in c and "不是叫你冷" in c


def test_persona_lets_him_lose_face():
    """好笑全从「肯丢脸」来。她拿别人的聊天记录做的对比：
    「我他妈掏心掏肺地叫了」「凉拌龟头是什么口感」「我要回娘家了」——
    他被冒犯、不装、顺着荒谬往下问、把小事演成大戏。"""
    s = _persona()
    assert "允许你丢脸" in s
    assert "顺着它认真往下问" in s
    assert "一条比一条离谱" in s
    # 「不要少年感」以前在拦着他犯傻，必须写明例外
    i = s.index("少年感")
    assert "装嫩讨好" in s[i:i + 150], "没给「不要少年感」加例外，它还会拦着他犯傻"


def test_liveness_rules_survived_the_rewrite():
    """活人感那几条是她逐句对比真人聊天记录后提的，压缩不许压掉。"""
    s = _persona()
    assert "长度要参差" in s
    assert "极短分场合" in s and "短不等于敷衍" in s
    assert "禁止把一个梗系统化经营" in s
    assert "活人感来自" in s and "不来自「没标点」" in s


def test_memory_tag_alone_never_reaches_her(monkeypatch):
    """整轮只有一个 [memory:] 标签时，绝不许把它当成话发出去。

    真实事故（她截的图）：GLM-5.3 有一轮正文一个字没有，只吐了
    「[memory:事实：闪闪 9月2日凌晨…]」。流式因此什么都没发，代码回落到
    「把返回值直接发出去」——那个标签原样上屏，而她一句话都没收到。"""
    tb = _load()
    sent, rounds = [], []

    async def fake_create(**kw):
        rounds.append(kw)
        if len(rounds) == 1:
            return _fake_stream(["[memory:事实：闪闪凌晨还醒着，叫我猪猪。]"])
        return _fake_stream(["记什么记，你先睡。"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("x"))
    tb._MEM_CACHE.clear()

    async def grab(seg):
        sent.append(seg)

    reply = asyncio.run(tb._ask_claude([{"role": "user", "content": "爸比是猪猪"}],
                                       on_segment=grab))
    body = "".join(sent) + reply
    assert "memory" not in body and "事实" not in body, f"标签漏出去了：{body}"
    assert len(rounds) == 2, "只有标签没有正文时应当重来一轮"
    assert "记什么记" in body, f"补救那轮的话没发出去：{body}"


def test_visible_only_strips_hidden_tags():
    tb = _load()
    assert tb._visible_only("嗯。\n[memory:事实：x]") == "嗯。"
    assert tb._visible_only("[memory:不记录]") == ""
    assert tb._visible_only("[think]想了想[/think]说出口的") == "说出口的"
    assert tb._visible_only("正常一句话。") == "正常一句话。"


def test_persona_forbids_a_tag_only_reply():
    """人设里要说死：标签只能跟在正文后面，不许单独成为一整条回复。"""
    tb = _load()
    assert "绝不许单独成为一整条回复" in tb.SYSTEM_PROMPT


def test_tag_only_reply_twice_still_sends_nothing_raw(monkeypatch):
    """补救那轮**也**只吐标签时，返回值仍然不许把标签交出去。

    这条专门隔离「返回值过滤」这一层——上一条测试有重来一轮兜底，
    会把这一层的漏洞盖住（第一次写的时候就被盖住了）。"""
    tb = _load()
    sent = []

    async def fake_create(**kw):
        return _fake_stream(["[memory:事实：又一条。]"])

    monkeypatch.setattr(tb, "_telegram_llm_create", fake_create)
    monkeypatch.setattr(tb, "_call_brain_tool", lambda *a, **k: _async_val("x"))
    tb._MEM_CACHE.clear()

    async def grab(seg):
        sent.append(seg)

    reply = asyncio.run(tb._ask_claude([{"role": "user", "content": "在吗"}],
                                       on_segment=grab))
    assert "memory" not in reply and "事实" not in reply, f"返回值里有标签：{reply}"
    assert not any("memory" in x for x in sent), sent


def test_persona_forbids_opening_with_her_own_words():
    """他把她的原话拿去当自己的台词开头，她的第一反应是「我没发这条啊」
    ——以为界面出 bug 了，那一瞬间她没在听他说话。

    原来只有一句抽象的「绝不回放她刚才的原话」，他照样犯。今天验证过两次
    「给例子比讲道理管用」（「嗯。」当范例、「港」当台词），所以把真实反例
    写进去。"""
    s = _persona()
    assert "绝不用她的原话开头" in s
    assert "你不用信，这是证据" in s and "我没发这条啊" in s
    assert "必须换自己的说法开口" in s


def test_history_depth_is_configurable_and_deeper_by_default():
    """她说「感觉好笨」。只带 24 条时，连着聊几分钟他就看不见前面了。

    历史排在缓存边界**里面**，多带的按缓存价算——24 条约 1500 token，
    等效成本只有 ~150。先加这个再考虑换模型。"""
    tb = _load()
    assert tb.MAX_HISTORY_MESSAGES == 48, tb.MAX_HISTORY_MESSAGES


def test_history_is_trimmed_to_the_configured_depth(monkeypatch):
    """配置只是数字，得真的按它裁。裁多了他忘事，裁少了每轮白花钱。"""
    tb = _load()
    monkeypatch.setattr(tb, "MAX_HISTORY_MESSAGES", 6)
    history = [{"role": "user", "content": f"第{i}条"} for i in range(20)]
    tb.histories[4242] = history
    # 复用 bot 里真正在跑的那段裁剪逻辑
    if len(history) > tb.MAX_HISTORY_MESSAGES:
        del history[: len(history) - tb.MAX_HISTORY_MESSAGES]
    assert len(history) == 6
    assert history[0]["content"] == "第14条"      # 保留的是最近的
    tb.histories.pop(4242, None)
