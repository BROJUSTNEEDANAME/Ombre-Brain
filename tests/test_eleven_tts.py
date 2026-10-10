"""ElevenLabs v3 嗓子：接口参数、唱歌标签、失败不静默。用替身真的跑 synth。"""
import asyncio

import pytest

import eleven_tts as E


class _Resp:
    def __init__(self, status=200, content=b"OggS" + b"x" * 500, text=""):
        self.status_code, self.content, self.text = status, content, text


class _Client:
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, params=None, headers=None, json=None):
        _Client.calls.append(dict(url=url, params=params, headers=headers, json=json))
        return _Client.resp


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(E, "API_KEY", "k-test")
    monkeypatch.setattr(E, "VOICE_ID", "v-nikto")
    monkeypatch.setattr(E.httpx, "AsyncClient", _Client)
    _Client.calls.clear()
    _Client.resp = _Resp()
    return _Client


def test_not_configured_means_no_voice(monkeypatch):
    monkeypatch.setattr(E, "API_KEY", "")
    monkeypatch.setattr(E, "VOICE_ID", "v")
    assert not E.configured()
    with pytest.raises(RuntimeError):
        asyncio.run(E.synth("你好"))


def test_request_matches_the_official_sdk_contract(wired):
    out = asyncio.run(E.synth("过来。‖坐好。"))
    assert out.startswith(b"OggS")
    c = wired.calls[0]
    assert c["url"] == "https://api.elevenlabs.io/v1/text-to-speech/v-nikto"
    assert c["params"] == {"output_format": "opus_48000_64"}
    assert c["headers"]["xi-api-key"] == "k-test"
    assert c["json"]["model_id"] == "eleven_v3", "她对比过：v3 可以，v4 容易变细"
    assert c["json"]["text"] == "过来。\n坐好。", "‖ 当停顿，不能原样送进合成器"
    assert c["json"]["voice_settings"]["stability"] == 0.5, "v3 只有三档，0.4 吸到 Natural"


def test_singing_is_detected_and_loosens_stability(wired):
    text = "[sings] Twinkle, twinkle, little star\n[sings] How I wonder what you are"
    assert E.wants_singing(text)
    asyncio.run(E.synth(text))
    assert wired.calls[0]["json"]["voice_settings"]["stability"] == 0.0, "v3 唱歌用 Creative"
    assert "[sings]" in wired.calls[0]["json"]["text"], "标签要原样交给 v3，那是它的指令"
    assert E.wants_singing("[singing quickly] la la la")
    assert E.wants_singing("[hums] mmm")
    assert not E.wants_singing("[whispers] 过来。")


def test_tags_are_stripped_from_the_text_fallback():
    t = "[sings] Twinkle twinkle [laughs] 唱完了。"
    assert E.strip_tags(t) == "Twinkle twinkle 唱完了。"
    assert E.strip_tags("过来。") == "过来。"
    # 中文方括号内容不是标签，别误删
    assert E.strip_tags("[你猜]") == "[你猜]"


def test_http_error_and_empty_audio_raise_instead_of_sending_a_dead_voice_note(wired):
    wired.resp = _Resp(status=401, text="bad key")
    with pytest.raises(RuntimeError, match="401"):
        asyncio.run(E.synth("你好"))
    wired.resp = _Resp(status=200, content=b"")
    with pytest.raises(RuntimeError, match="空音频"):
        asyncio.run(E.synth("你好"))


# ── 送进嗓子之前的清洗（她捏好嗓子一测「不太好」：动作括号被原样念了出来）──

def test_action_parens_inner_voice_emoji_and_stamps_are_not_spoken(wired):
    reply = ("[2026-10-10 21:04] *（她又在逞强。）*\n"
             "（把你按进怀里）……别动 乖。(｡•ᴗ•｡)😊\n"
             "（小尼：尾巴拍床 (｀へ´)）")
    asyncio.run(E.synth(reply))
    sent = wired.calls[0]["json"]["text"]
    assert sent == "……别动，乖。", sent
    for bad in ("按进怀里", "逞强", "小尼", "21:04", "😊", "｡"):
        assert bad not in sent, bad


def test_sound_like_actions_become_light_tags_not_words():
    assert E.prepare_text("（低笑）过来。") == "[quiet laugh] 过来。"
    assert E.prepare_text("（叹气）睡吧。") == "[sighs] 睡吧。"
    assert E.prepare_text("（贴着你耳朵）乖。") == "[low and close] 乖。"


def test_bold_is_spoken_but_star_actions_are_not():
    assert E.prepare_text("**乖**。") == "乖。"
    assert E.prepare_text("*揉你头发*\n过来。") == "过来。"


def test_loud_tags_are_dropped_and_tags_capped_at_three():
    assert E.prepare_text("[intense, growling] 过来。") == "过来。"
    assert E.prepare_text("[heavy breathing] 嗯。") == "嗯。"
    out = E.prepare_text("[low] a [soft] b [warmly] c [sighs] d")
    assert out.count("[") == 3 and out.endswith("d") and "[sighs]" not in out, out
    # 唱歌标签不受上限影响：四句都要唱
    song = "\n".join(f"[sings] line {i}" for i in range(4))
    assert E.prepare_text(song).count("[sings]") == 4


def test_space_separated_chinese_gets_pauses_but_mixed_text_is_untouched():
    assert E.prepare_text("过来 坐好") == "过来，坐好"
    assert E.prepare_text("girl 过来") == "girl 过来"


def test_reply_that_is_only_actions_raises_so_the_bridge_falls_back_to_text(wired):
    with pytest.raises(ValueError):
        asyncio.run(E.synth("（抱紧）"))
    assert wired.calls == [], "全是动作就别去花字数合成一条空语音"



# ── v4 不认就用同一副嗓子退回 v3 ──

class _Seq(_Client):
    """按顺序回不同的响应。"""
    seq = []

    async def post(self, url, params=None, headers=None, json=None):
        _Client.calls.append(dict(url=url, params=params, headers=headers, json=json))
        return _Seq.seq.pop(0)


def test_v4_settings_only_carry_what_v4_accepts():
    assert E._settings("eleven_v4", False) == {"stability": E.STABILITY, "similarity_boost": 0.8}


def test_v3_stability_is_always_one_of_its_three_steps(monkeypatch):
    for x, want in ((0.1, 0.0), (0.3, 0.5), (0.4, 0.5), (0.8, 1.0)):
        monkeypatch.setattr(E, "STABILITY", x)
        assert E._settings("eleven_v3", False)["stability"] == want, x


def test_v4_rejected_falls_back_to_v3_with_the_same_voice(wired, monkeypatch):
    monkeypatch.setattr(E, "MODEL_ID", "eleven_v4")
    monkeypatch.setattr(E.httpx, "AsyncClient", _Seq)
    _Seq.seq = [_Resp(status=400, content=b"", text="model not available"), _Resp()]
    out = asyncio.run(E.synth("[warmly] Come here. I missed you."))
    assert out.startswith(b"OggS")
    a, b = wired.calls
    assert a["json"]["model_id"] == "eleven_v4" and b["json"]["model_id"] == "eleven_v3"
    assert a["url"] == b["url"], "退回的是同一副嗓子，不许换嗓"
    assert "style" in b["json"]["voice_settings"], "v3 的设置照旧"


def test_both_models_failing_raises_with_both_reasons(wired, monkeypatch):
    monkeypatch.setattr(E, "MODEL_ID", "eleven_v4")
    monkeypatch.setattr(E.httpx, "AsyncClient", _Seq)
    _Seq.seq = [_Resp(status=400, content=b"", text="v4 nope"),
                _Resp(status=401, content=b"", text="bad key")]
    with pytest.raises(RuntimeError) as e:
        asyncio.run(E.synth("Come here."))
    assert "v4 nope" in str(e.value) and "bad key" in str(e.value)


def test_pinned_to_v3_does_not_retry(wired, monkeypatch):
    monkeypatch.setattr(E, "MODEL_ID", "eleven_v3")
    monkeypatch.setattr(E.httpx, "AsyncClient", _Seq)
    _Seq.seq = [_Resp(status=500, content=b"", text="down")]
    with pytest.raises(RuntimeError):
        asyncio.run(E.synth("Come here."))
    assert len(wired.calls) == 1
