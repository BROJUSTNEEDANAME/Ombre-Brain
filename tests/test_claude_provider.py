"""Claude 档位的真跑测试。

存在的理由：这层是「翻译」，翻错了不会报语法错——只会在她那边表现成
「换成 claude 之后他不说话了」。所以这里把翻译两个方向都真的跑一遍，
并且用替身把整条 _ask_claude 在 claude 模型下走通。
"""
import asyncio
import json
import sys
import types

import pytest

import claude_provider as cp


# ---------------------------------------------------------------- 进（翻译入参）

def test_system_is_split_out_and_first_message_is_user():
    system, msgs = cp.convert_messages([
        {"role": "system", "content": "你是他"},
        {"role": "assistant", "content": "掉队的开场白"},   # 必须被丢掉
        {"role": "user", "content": "在吗"},
    ])
    assert system == "你是他"
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == [{"type": "text", "text": "在吗"}]


def test_tool_call_round_trip_becomes_native_blocks():
    """OpenAI 的 assistant.tool_calls + role=tool → tool_use / tool_result。"""
    _, msgs = cp.convert_messages([
        {"role": "user", "content": "还记得吗"},
        {"role": "assistant", "content": "我想想",
         "tool_calls": [{"id": "c1", "type": "function",
                         "function": {"name": "breath", "arguments": '{"query":"过去"}'}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "（记忆）"},
    ])
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"][1] == {"type": "tool_use", "id": "c1",
                                     "name": "breath", "input": {"query": "过去"}}
    assert msgs[2]["role"] == "user"
    assert msgs[2]["content"][0]["type"] == "tool_result"


def test_consecutive_same_role_messages_are_merged():
    """她连发好几条 → 相邻 user 必须合并，否则 Anthropic 直接 400。"""
    _, msgs = cp.convert_messages([
        {"role": "user", "content": "在吗"},
        {"role": "user", "content": "睡了？"},
    ])
    assert len(msgs) == 1 and len(msgs[0]["content"]) == 2


def test_image_is_translated_to_native_base64_block():
    _, msgs = cp.convert_messages([{"role": "user", "content": [
        {"type": "text", "text": "看这个"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
    ]}])
    assert msgs[0]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}


def test_build_kwargs_caches_persona_and_maps_tool_choice_none():
    kw = cp.build_kwargs(
        model="claude-opus-5", max_tokens=100,
        messages=[{"role": "system", "content": "人设"}, {"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {
            "name": "breath", "description": "d", "parameters": {"type": "object"}}}],
        tool_choice="none", thinking=True)
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert kw["tools"][0] == {"name": "breath", "description": "d",
                              "input_schema": {"type": "object"}}
    assert kw["tool_choice"] == {"type": "none"}
    assert kw["thinking"] == {"type": "adaptive"}   # 4.6+ 不许再传 budget_tokens
    assert "budget_tokens" not in json.dumps(kw)


# ---------------------------------------------------------------- 出（翻译返回）

def _blk(**kw):
    return types.SimpleNamespace(**kw)


def test_shape_response_reads_text_and_tool_use():
    resp = cp.shape_response(_blk(content=[
        _blk(type="text", text="嗯。"),
        _blk(type="tool_use", id="c9", name="hold", input={"content": "x"}),
    ], usage=None))
    msg = resp.choices[0].message
    assert msg.content == "嗯。"
    assert msg.tool_calls[0].function.name == "hold"
    assert json.loads(msg.tool_calls[0].function.arguments) == {"content": "x"}


class _RawStream:
    def __init__(self, events):
        self._events = events
        self.closed = False

    async def __aiter__(self):
        for e in self._events:
            yield e

    async def close(self):
        self.closed = True


def test_stream_events_become_openai_shaped_chunks():
    asyncio.run(_stream_case())


async def _stream_case():
    raw = _RawStream([
        _blk(type="content_block_delta", index=0,
             delta=_blk(type="thinking_delta", thinking="内心戏")),      # 不许外推
        _blk(type="content_block_delta", index=0,
             delta=_blk(type="text_delta", text="醒了？")),
        _blk(type="content_block_start", index=1,
             content_block=_blk(type="tool_use", id="c1", name="breath")),
        _blk(type="content_block_delta", index=1,
             delta=_blk(type="input_json_delta", partial_json='{"q":')),
        _blk(type="content_block_delta", index=1,
             delta=_blk(type="input_json_delta", partial_json='"x"}')),
    ])
    text, acc = "", {}
    async for chunk in cp.Stream(raw):
        d = chunk.choices[0].delta
        if d.content:
            text += d.content
        for tc in (d.tool_calls or []):
            slot = acc.setdefault(tc.index, {"name": "", "args": ""})
            if tc.function.name:
                slot["name"] = tc.function.name
            if tc.function.arguments:
                slot["args"] += tc.function.arguments
    assert text == "醒了？"                      # 内心戏没混进正文
    assert acc[0] == {"name": "breath", "args": '{"q":"x"}'}


def test_missing_key_says_so_instead_of_crashing_later(monkeypatch):
    monkeypatch.setattr(cp, "_client", None)
    monkeypatch.delenv("OMBRE_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OMBRE_ANTHROPIC_KEY"):
        cp.client()


def test_stream_reports_token_usage_with_cache_hits():
    """流式这条路必须报用量，否则 /cache 里日常聊天永远是 0。

    Anthropic 的缓存命中算在 cache_read_input_tokens 里，**不计入**
    input_tokens——所以「这轮送进去多少」得三项相加，算错了命中率会虚高。"""
    raw = _RawStream([
        _blk(type="message_start", message=_blk(usage=_blk(
            input_tokens=120, cache_read_input_tokens=4000,
            cache_creation_input_tokens=80, output_tokens=1))),
        _blk(type="content_block_delta", index=0,
             delta=_blk(type="text_delta", text="在。")),
        _blk(type="message_delta", usage=_blk(output_tokens=42)),
    ])
    st = cp.Stream(raw)

    async def _drain():
        async for _ in st:
            pass

    asyncio.run(_drain())
    assert st.usage.prompt_tokens == 4200        # 120 + 4000 + 80
    assert st.usage.prompt_tokens_details.cached_tokens == 4000
    assert st.usage.completion_tokens == 42

    # 必须能被统计模块直接读懂
    from prompt_cache import cache_usage
    assert cache_usage(st.usage) == (4200, 4000)


def test_stream_without_usage_events_leaves_usage_none():
    """没给用量的流不许伪造一个 0，那会把命中率算低。"""
    st = cp.Stream(_RawStream([
        _blk(type="content_block_delta", index=0,
             delta=_blk(type="text_delta", text="嗯")),
    ]))

    async def _drain():
        async for _ in st:
            pass

    asyncio.run(_drain())
    assert st.usage is None


def test_off_thinking_asks_to_disable_then_gives_up_gracefully():
    """「不开思考」那档先试着关；这代不认就记住，去掉字段重来——
    绝不因为一个参数让她收不到回复。"""
    cp._no_disable.discard("claude-opus-4-6")
    kw = cp.build_kwargs(model="claude-opus-4-6", max_tokens=10,
                         messages=[{"role": "user", "content": "hi"}], thinking=False)
    assert kw["thinking"] == {"type": "disabled"}

    calls = []

    class _Msgs:
        async def create(self, **k):
            calls.append(k)
            if "thinking" in k:
                raise RuntimeError("thinking: disabled is not supported for this model")
            return types.SimpleNamespace(content=[_blk(type="text", text="在")], usage=None)

    class _C:
        messages = _Msgs()

    cp._client = _C()
    try:
        resp = asyncio.run(cp.create(model="claude-opus-4-6", max_tokens=10,
                                     messages=[{"role": "user", "content": "hi"}],
                                     thinking=False))
    finally:
        cp._client = None
    assert resp.choices[0].message.content == "在"
    assert len(calls) == 2 and "thinking" not in calls[1]   # 第二次不带这个字段
    assert "claude-opus-4-6" in cp._no_disable              # 记住了，下次不再白试
    cp._no_disable.discard("claude-opus-4-6")


def test_thinking_on_never_gets_downgraded():
    """开思考那档报错就如实抛出：不重试、不悄悄降级成不思考。

    只断言「抛了个带 thinking 的错」太弱——降不降级它都抛。所以这里数调用次数：
    开思考只许发一次请求。"""
    calls = []

    class _Msgs:
        async def create(self, **k):
            calls.append(k)
            raise RuntimeError("thinking: something went wrong")

    class _C:
        messages = _Msgs()

    cp._no_disable.discard("claude-opus-4-6")
    cp._client = _C()
    try:
        with pytest.raises(RuntimeError, match="thinking"):
            asyncio.run(cp.create(model="claude-opus-4-6", max_tokens=10,
                                  messages=[{"role": "user", "content": "hi"}],
                                  thinking=True))
    finally:
        cp._client = None
    assert len(calls) == 1, f"开思考不该重试，实际发了 {len(calls)} 次"
    assert calls[0]["thinking"] == {"type": "adaptive"}
    assert "claude-opus-4-6" not in cp._no_disable   # 这不是「关不掉」，别记错账


# --------------------------------------------------------------------------
# 缓存布局（对着 NyraSeithhh/cache 那份文档校）
# --------------------------------------------------------------------------

def test_persona_block_uses_one_hour_ttl_by_default(monkeypatch):
    """她是隔十几二十分钟回一句的节奏，正好落在官方说的「5~60 分钟用 1h」那档。"""
    monkeypatch.delenv("OMBRE_CLAUDE_CACHE_TTL", raising=False)
    kw = cp.build_kwargs(model="claude-opus-4-6", max_tokens=10,
                         messages=[{"role": "system", "content": "人设"},
                                   {"role": "user", "content": "hi"}])
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}

    monkeypatch.setenv("OMBRE_CLAUDE_CACHE_TTL", "5m")
    kw = cp.build_kwargs(model="claude-opus-4-6", max_tokens=10,
                         messages=[{"role": "system", "content": "人设"},
                                   {"role": "user", "content": "hi"}])
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_rolling_breakpoint_lands_on_history_not_on_this_turn():
    """断点要打在「本轮之前」的 assistant 上。

    打在最后一条（本轮新内容）上等于每轮只写不读——纯多花钱，比不打还糟。"""
    kw = cp.build_kwargs(model="claude-opus-4-6", max_tokens=10, messages=[
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3-本轮"},
    ])
    marked = [(i, m["role"], b) for i, m in enumerate(kw["messages"])
              for b in m["content"] if "cache_control" in b]
    assert len(marked) == 1, marked
    i, role, block = marked[0]
    assert role == "assistant" and block["text"] == "a2"
    assert i < len(kw["messages"]) - 1          # 绝不是最后一条
    # 短 TTL 的条目必须排在长 TTL 的后面：system 是 1h，这个是默认 5m
    assert block["cache_control"] == {"type": "ephemeral"}


def test_first_turn_has_no_rolling_breakpoint():
    """第一轮没有历史，打了也读不到，别白写。"""
    kw = cp.build_kwargs(model="claude-opus-4-6", max_tokens=10, messages=[
        {"role": "system", "content": "人设"},
        {"role": "user", "content": "在吗"},
    ])
    assert not [b for m in kw["messages"] for b in m["content"] if "cache_control" in b]


def test_build_kwargs_is_idempotent_on_the_same_history():
    """同一份历史连调两次，出来的必须一模一样。

    断点是渲染期的事。要是它把标记攒进了调用方的历史，第二轮的字节就和第一轮
    不同了——缓存自己把自己搞废。这条同时钉住「调用方历史不被改写」。

    （说明：目前 convert_messages 本来就会重建每个 block，所以这条我没能造出
    会让它变红的变异；它是结构性护栏，防以后有人把 block 改成直接复用原对象。）"""
    history = [{"role": "user", "content": "u1"},
               {"role": "assistant", "content": "a1"},
               {"role": "user", "content": "u2"}]
    snapshot = json.dumps(history, ensure_ascii=False)

    def _build():
        return json.dumps(cp.build_kwargs(
            model="claude-opus-4-6", max_tokens=10,
            messages=[{"role": "system", "content": "人设"}, *history]),
            ensure_ascii=False, sort_keys=True)

    assert _build() == _build()
    assert json.dumps(history, ensure_ascii=False) == snapshot
