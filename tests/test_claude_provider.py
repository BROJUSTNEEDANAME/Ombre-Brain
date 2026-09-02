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
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
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
