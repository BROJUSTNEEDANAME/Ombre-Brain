"""语音条：清洗、双语字幕、声口、变速。真的跑 ffmpeg，人声用 ffmpeg 现场合成的替身。"""
import asyncio
import shutil
import subprocess

import pytest

import eleven_tts
import voice_mix as V

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="没有 ffmpeg")


def _voice(secs=1.0):
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={secs}",
           "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-f", "ogg", "pipe:1"]
    return subprocess.run(cmd, capture_output=True, check=True).stdout


@pytest.fixture
def fake_synth(monkeypatch):
    calls = []

    async def synth(text, singing=None, **k):
        calls.append(text)
        return _voice(1.0)

    monkeypatch.setattr(eleven_tts, "synth", synth)
    return calls


def _secs(ogg: bytes) -> float:
    return len(V.decode(ogg)) / V.SR


# ── 他旧对话里留下的亲吻行、wet 记号：送进嗓子前摘干净 ──

def test_strip_wet_and_markers():
    assert V.strip_wet("[low, close, wet] a") == "[low, close] a"
    assert V.strip_wet("[wet] a") == " a"
    assert V.strip_wet("[low and wet] a") == "[low] a"
    assert V.strip_wet("[low] wet hair") == "[low] wet hair", "台词里的 wet 不动，只动标签"
    assert V.strip_markers("a\n[kiss]\n[soft, wet] b") == "a\n[soft] b"
    assert V.strip_markers("a‖[kiss]‖b") == "a‖b", "‖ 是分条记号，不许动"
    # 字幕里也不许出现亲吻行
    assert eleven_tts.strip_tags(V.strip_markers("Come.\n[kiss deep]\nStay.")) == "Come.\nStay."


def test_old_markers_never_reach_the_voice(fake_synth, monkeypatch):
    """声音效果拆掉了，但他可能照旧写 [kiss] 和 wet——嗓子看到会念出来，必须摘掉。"""
    monkeypatch.setattr(V, "have_ffmpeg", lambda: False)
    out = asyncio.run(V.render("[low] Come here.\n[kiss]\n[soft, wet] Stay."))
    assert out[:4] == b"OggS"
    assert fake_synth == ["[low] Come here.\n[soft] Stay."], "整段一次念完，标记去干净"


# ── 变速 ──

@needs_ffmpeg
def test_plain_reply_is_one_synth_slowed_a_little(fake_synth, monkeypatch):
    monkeypatch.setattr(V, "TEMPO", 0.93)
    out = asyncio.run(V.render("[low] Come here. Stay with me."))
    assert len(fake_synth) == 1
    assert 1.03 < _secs(out) < 1.12, f"慢一点点（0.93 倍）：{_secs(out)}"


@needs_ffmpeg
def test_singing_keeps_its_tempo(fake_synth):
    out = asyncio.run(V.render("[sings] la la la"))
    assert 0.95 < _secs(out) < 1.05


@needs_ffmpeg
def test_speed_can_go_faster_and_one_means_untouched(fake_synth, monkeypatch):
    monkeypatch.setattr(V, "TEMPO", 1.1)
    assert 0.85 < _secs(asyncio.run(V.render("Come here."))) < 0.95
    monkeypatch.setattr(V, "TEMPO", 1.0)
    assert 0.95 < _secs(asyncio.run(V.render("Come here."))) < 1.05


def test_slowdown_failure_still_sends_the_voice(fake_synth, monkeypatch):
    def boom(a):
        raise RuntimeError("ffmpeg 炸了")
    monkeypatch.setattr(V, "slow_down", boom)
    monkeypatch.setattr(V, "have_ffmpeg", lambda: True)
    out = asyncio.run(V.render("Come here."))
    assert out[:4] == b"OggS"


# ── 双语字幕：「译：」行不念，只进字幕 ──

def test_translation_lines_are_dropped_for_the_voice_and_shown_for_her():
    reply = "[low] Come here.\n译：过来。\nDon't move.\n翻译: 别动。‖Stay.‖译：留下。"
    assert V.drop_translations(reply) == "[low] Come here.\nDon't move.\nStay."
    assert V.show_translations(reply) == "[low] Come here.\n过来。\nDon't move.\n别动。‖Stay.‖留下。"
    # 台词里提到「译」字但不是译文行，不动
    assert V.drop_translations("他说译：不对") == "他说译：不对"


def test_render_never_sends_the_translation_to_the_voice(fake_synth, monkeypatch):
    monkeypatch.setattr(V, "have_ffmpeg", lambda: False)
    asyncio.run(V.render("[low] Come here.\n译：过来。"))
    assert fake_synth == ["[low] Come here."]


# ── 她手动定的声口（/tone）──

def test_parse_tone_maps_chinese_and_keeps_english():
    assert V.parse_tone("慵懒 低沉") == ("lazy, relaxed, low", [])
    assert V.parse_tone("relaxed, Warm") == ("relaxed, warm", [])
    assert V.parse_tone("温柔、乱写") == ("tender", ["乱写"])


def test_apply_tone_replaces_his_opening_tag():
    assert V.apply_tone("[soft] Come here.\n[low] Stay.", "lazy") == "[lazy] Come here.\n[low] Stay."
    assert V.apply_tone("Come here.", "lazy") == "[lazy] Come here."
    assert V.apply_tone("[kiss]\n译：亲\n[low] Easy.", "tender") == "[kiss]\n译：亲\n[tender] Easy."
    assert V.apply_tone("[soft] a", "") == "[soft] a"
