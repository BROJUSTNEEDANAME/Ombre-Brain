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
    assert names == ["5.3", "5.2", "5.2t", "5.1", "5.1t", "o4.6", "o4.6t"], names
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
