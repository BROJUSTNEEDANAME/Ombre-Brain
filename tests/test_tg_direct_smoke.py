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
        return _fake_stream(["醒了？\n\n", "先喝水 桌上那杯"])

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

    assert sent == ["醒了？", "先喝水 桌上那杯"], sent
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
