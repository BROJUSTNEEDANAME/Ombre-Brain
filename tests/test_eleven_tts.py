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
    assert c["json"]["model_id"] == "eleven_v3"
    assert c["json"]["text"] == "过来。\n坐好。", "‖ 当停顿，不能原样送进合成器"
    assert c["json"]["voice_settings"]["stability"] == 0.5


def test_singing_is_detected_and_loosens_stability(wired):
    text = "[sings] Twinkle, twinkle, little star\n[sings] How I wonder what you are"
    assert E.wants_singing(text)
    asyncio.run(E.synth(text))
    assert wired.calls[0]["json"]["voice_settings"]["stability"] == 0.3
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
