"""接 Anthropic 官方 API 的适配层（/model 里那几个 claude 档位就走这里）。

为什么要这么一层：整个 telegram_bot._ask_claude 是照 OpenAI 那套报文写的
（messages 里 role=tool、assistant.tool_calls、流式 delta.tool_calls）。
Claude 的原生报文完全不一样——工具是 content block、工具结果是 user 消息里的
tool_result、流式是 content_block_delta 事件。

两条路：把 _ask_claude 拆成两套（改动大、气泡分段/去重/掐断那些坑要踩第二遍），
或者只在**边界**上翻译一次。选后者：这里用 anthropic 官方 SDK 发真正的原生请求，
只把「进」和「出」翻译成上层已经在用的形状。上层一行不用改，GLM 那条路也不受影响。

⚠️ 不是「用 OpenAI 兼容接口去调 Claude」——那样会丢掉 thinking、缓存这些；
这里发出去的是 Anthropic 原生请求，翻译只发生在本文件里。
"""

from __future__ import annotations

import base64
import json
import types
import os
import re
from typing import Any


def is_claude_model(model: str | None) -> bool:
    return str(model or "").startswith("claude-")


_client = None


def client():
    """懒加载：没选 claude 档位的时候不碰这个 SDK，也不要求装/配 key。"""
    global _client
    if _client is None:
        # 先查 key 再 import：缺 key 是最常见的情况，要给一句人话，
        # 不是一个 ModuleNotFoundError。
        key = (
            os.environ.get("OMBRE_ANTHROPIC_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or ""
        ).strip()
        if not key:
            raise RuntimeError(
                "没配 Claude 的 key：在 .env.apibot 里加 OMBRE_ANTHROPIC_KEY=..."
            )
        try:
            from anthropic import AsyncAnthropic  # noqa: PLC0415
        except ModuleNotFoundError as exc:  # noqa: BLE001
            raise RuntimeError(
                "没装 anthropic：pip install -r requirements-telegram.txt"
            ) from exc
        _client = AsyncAnthropic(
            api_key=key,
            timeout=float(os.environ.get("OMBRE_LLM_TIMEOUT", "60")),
            max_retries=0,
        )
    return _client


# ---------------------------------------------------------------------------
# 进：OpenAI 形状 → Anthropic 原生
# ---------------------------------------------------------------------------

_DATA_URL_RE = re.compile(r"^data:([\w./+-]+);base64,(.*)$", re.S)


def _image_block(url: str) -> dict | None:
    m = _DATA_URL_RE.match(url or "")
    if m:
        return {"type": "image",
                "source": {"type": "base64", "media_type": m.group(1), "data": m.group(2)}}
    if str(url).startswith("http"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _user_content(content: Any) -> list[dict]:
    """user 消息正文：字符串直接包成 text；图片列表逐块翻译。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content.strip() else []
    out: list[dict] = []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            if str(block.get("text") or "").strip():
                out.append({"type": "text", "text": block["text"]})
        elif block.get("type") == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            img = _image_block(url)
            if img:
                out.append(img)
    return out


def convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """拆出 system，其余翻成 Anthropic 的 messages。

    Anthropic 的硬性要求：system 单独传、第一条必须是 user、相邻同 role 要合并、
    工具结果得放在紧跟着的那条 user 消息里。这里一次性满足。"""
    system_parts: list[str] = []
    out: list[dict] = []

    def _push(role: str, blocks: list[dict]) -> None:
        if not blocks:
            return
        if out and out[-1]["role"] == role:
            out[-1]["content"].extend(blocks)
        else:
            out.append({"role": role, "content": blocks})

    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content")
        if role == "system":
            if isinstance(content, str) and content.strip():
                system_parts.append(content)
        elif role == "user":
            _push("user", _user_content(content))
        elif role == "assistant":
            blocks: list[dict] = []
            if isinstance(content, str) and content.strip():
                blocks.append({"type": "text", "text": content})
            for tc in msg.get("tool_calls") or []:
                fn = (tc.get("function") or {}) if isinstance(tc, dict) else {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:  # noqa: BLE001
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": (tc.get("id") if isinstance(tc, dict) else None) or "call_0",
                    "name": fn.get("name") or "",
                    "input": args if isinstance(args, dict) else {},
                })
            _push("assistant", blocks)
        elif role == "tool":
            _push("user", [{
                "type": "tool_result",
                "tool_use_id": msg.get("tool_call_id") or "call_0",
                "content": str(msg.get("content") or "")[:8000],
            }])

    while out and out[0]["role"] != "user":   # 第一条必须是 user
        out.pop(0)
    return "\n\n".join(system_parts), out


def convert_tools(tools: list[dict] | None) -> list[dict]:
    out = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t, dict) else None
        if not fn:
            continue
        out.append({
            "name": fn.get("name"),
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def convert_tool_choice(choice: Any) -> dict | None:
    if choice in (None, "", "auto"):
        return None
    if choice == "none":
        return {"type": "none"}
    if choice == "required":
        return {"type": "any"}
    return None


# 哪些模型不认 thinking={"type":"disabled"}。Opus 4.6 这代文档只写了「支持
# 自适应思考」，没承诺能关；Opus 5 能关但只在 effort<=high。所以做法是：
# 先试着关，被拒就记住这个模型、以后直接不带这个字段（和 GLM 那套档位协商同理）。
_no_disable: set[str] = set()

# 缓存 TTL。官方给的选法是看「两次请求开头的间隔」：
#   <5 分钟   → 5m（每次请求都会刷新计时，最便宜）
#   5~60 分钟 → 1h（唯一值得付 2 倍写入费的区间）
#   >1 小时   → 都救不了，冷启动
# 她是隔十几二十分钟回一句的节奏，正好落在中间那档，所以默认 1h。
# 想改：OMBRE_CLAUDE_CACHE_TTL=5m
def _ttl() -> str:
    return "1h" if os.environ.get(
        "OMBRE_CLAUDE_CACHE_TTL", "1h").strip().lower() != "5m" else "5m"


def _cc(ttl: str) -> dict:
    """5m 是默认值，显式写出来反而多一个字段；1h 才需要带 ttl。"""
    return {"type": "ephemeral"} if ttl == "5m" else {"type": "ephemeral", "ttl": ttl}


def mark_rolling_breakpoint(msgs: list[dict]) -> None:
    """在「本轮之前」的最后一条 assistant 上打一个断点，把历史纳进缓存。

    默认缓存边界只到 system 末尾，历史对话每轮都按原价重算。这里再打一个标，
    边界一路下移到包含全部历史——多轮长会话省最多的就是它
    （NyraSeithhh/cache 的 BP4）。

    ⚠️ 打在 assistant 上、不打在最后一条消息上：最后那条是本轮新内容，
    下一轮长得不一样，打上去等于每轮只写不读，纯多花钱。
    ⚠️ 这一步的前提是历史逐字节稳定——所以易变内容必须用
    append_volatile_context 放到末尾，而不是塞进历史里。
    """
    for i in range(len(msgs) - 2, -1, -1):     # 跳过最后一条（本轮的）
        if msgs[i].get("role") != "assistant":
            continue
        blocks = msgs[i].get("content") or []
        if blocks and isinstance(blocks[-1], dict):
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            msgs[i] = {**msgs[i], "content": [*blocks[:-1], blocks[-1]]}
        return


def build_kwargs(*, model: str, messages: list[dict], max_tokens: int,
                 tools=None, tool_choice=None, thinking: bool = False,
                 allow_disable: bool = True) -> dict:
    system, msgs = convert_messages(messages)
    kw: dict[str, Any] = {"model": model, "max_tokens": int(max_tokens), "messages": msgs}
    if system:
        # 人设是每轮一模一样的长前缀，缓存下来能省一大笔钱、也更快。
        # tools 排在 system 前面，所以这一个标同时把 tools + system 都缓存了。
        kw["system"] = [{"type": "text", "text": system,
                         "cache_control": _cc(_ttl())}]
    # 长 TTL 的条目必须排在短的前面：system 用 1h，历史断点用默认 5m，顺序正确。
    mark_rolling_breakpoint(msgs)
    tl = convert_tools(tools)
    if tl:
        kw["tools"] = tl
        tc = convert_tool_choice(tool_choice)
        if tc:
            kw["tool_choice"] = tc
    if thinking:
        # 4.6 起是自适应思考，不再传 budget_tokens（传了会 400）
        kw["thinking"] = {"type": "adaptive"}
    elif allow_disable and model not in _no_disable:
        kw["thinking"] = {"type": "disabled"}
    return kw


# ---------------------------------------------------------------------------
# 出：Anthropic 原生 → 上层在用的 OpenAI 形状
# ---------------------------------------------------------------------------

class _Fn:
    def __init__(self, name=None, arguments=None):
        self.name, self.arguments = name, arguments


class _ToolCall:
    def __init__(self, index=0, id=None, name=None, arguments=None):  # noqa: A002
        self.index, self.id = index, id
        self.function = _Fn(name, arguments)


class _Msg:
    def __init__(self, content, tool_calls):
        self.content, self.tool_calls = content, tool_calls


class _Choice:
    def __init__(self, message=None, delta=None):
        self.message, self.delta = message, delta


class _Resp:
    def __init__(self, choices, usage=None):
        self.choices, self.usage = choices, usage


class _Delta:
    def __init__(self, content=None, tool_calls=None):
        self.content, self.tool_calls = content, tool_calls


def shape_response(message: Any) -> _Resp:
    """非流式返回：拼成 resp.choices[0].message.content / .tool_calls。"""
    text, calls = "", []
    for i, block in enumerate(getattr(message, "content", None) or []):
        btype = getattr(block, "type", None)
        if btype == "text":
            text += getattr(block, "text", "") or ""
        elif btype == "tool_use":
            calls.append(_ToolCall(
                index=i,
                id=getattr(block, "id", None) or f"call_{i}",
                name=getattr(block, "name", "") or "",
                arguments=json.dumps(getattr(block, "input", None) or {}, ensure_ascii=False),
            ))
    return _Resp([_Choice(message=_Msg(text, calls))], getattr(message, "usage", None))


class _Usage:
    """翻成 prompt_cache.cache_usage() 认识的形状。

    Anthropic 的字段是 input_tokens / cache_read_input_tokens /
    cache_creation_input_tokens；缓存命中的那部分算在 cache_read 里，
    它不计入 input_tokens，所以「这轮一共送进去多少」要三项相加。"""

    def __init__(self, raw):
        read = int(getattr(raw, "cache_read_input_tokens", 0) or 0)
        write = int(getattr(raw, "cache_creation_input_tokens", 0) or 0)
        self.prompt_tokens = int(getattr(raw, "input_tokens", 0) or 0) + read + write
        self.completion_tokens = int(getattr(raw, "output_tokens", 0) or 0)
        self.prompt_tokens_details = types.SimpleNamespace(cached_tokens=read)


class Stream:
    """把 Anthropic 的事件流翻成上层那个 `async for ch in st` 循环认识的块。

    上层只用三样东西：ch.choices[0].delta.content、delta.tool_calls（带
    index/id/function.name/function.arguments 增量）、以及 await st.close()。
    另外把这轮的 token 用量挂在 .usage 上，好让缓存命中率统计得到流式这条路
    ——不然 /cache 只统计得到后台调用，她日常聊天那部分全是空白。"""

    def __init__(self, raw):
        self._raw = raw
        self.usage = None

    async def close(self) -> None:
        try:
            await self._raw.close()
        except Exception:  # noqa: BLE001
            pass

    async def __aiter__(self):
        idx_of: dict[int, int] = {}
        async for event in self._raw:
            etype = getattr(event, "type", "")
            if etype == "message_start":
                u = getattr(getattr(event, "message", None), "usage", None)
                if u is not None:
                    self.usage = _Usage(u)      # 入参用量（含缓存命中）在开头就给
            elif etype == "message_delta":
                u = getattr(event, "usage", None)
                if u is not None and self.usage is not None:
                    self.usage.completion_tokens = int(
                        getattr(u, "output_tokens", 0) or 0)
            elif etype == "content_block_start":
                block = getattr(event, "content_block", None)
                if getattr(block, "type", None) == "tool_use":
                    i = getattr(event, "index", 0) or 0
                    idx_of[i] = len(idx_of)
                    yield _Resp([_Choice(delta=_Delta(tool_calls=[_ToolCall(
                        index=idx_of[i],
                        id=getattr(block, "id", None) or f"call_{i}",
                        name=getattr(block, "name", "") or "",
                        arguments="",
                    )]))])
            elif etype == "content_block_delta":
                delta = getattr(event, "delta", None)
                dtype = getattr(delta, "type", "")
                if dtype == "text_delta":
                    yield _Resp([_Choice(delta=_Delta(content=getattr(delta, "text", "")))])
                elif dtype == "input_json_delta":
                    i = getattr(event, "index", 0) or 0
                    yield _Resp([_Choice(delta=_Delta(tool_calls=[_ToolCall(
                        index=idx_of.get(i, i),
                        arguments=getattr(delta, "partial_json", "") or "",
                    )]))])
                # thinking_delta：内心戏，不外推


async def create(*, stream: bool = False, thinking: bool = False, **kwargs):
    """对外唯一入口：形状和 llm.chat.completions.create 一致。

    「关思考」那档会先试 thinking={"type":"disabled"}；这一代模型要是不认，
    记下来、去掉这个字段重来一次，绝不因为一个参数让整轮说不出话。"""
    model = kwargs.get("model", "")
    c = client()
    for _attempt in (1, 2):
        kw = build_kwargs(thinking=thinking, **kwargs)
        try:
            if stream:
                return Stream(await c.messages.create(stream=True, **kw))
            return shape_response(await c.messages.create(**kw))
        except Exception as e:  # noqa: BLE001
            if "thinking" not in str(e).lower() or model in _no_disable or thinking:
                raise
            _no_disable.add(model)   # 这代关不掉：以后都不带这个字段
    raise RuntimeError("thinking 参数协商失败")
