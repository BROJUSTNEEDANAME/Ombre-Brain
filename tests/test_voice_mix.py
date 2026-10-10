"""语音条拼接：台词 + 亲盒 + 水声。真的跑 ffmpeg，素材用 ffmpeg 现场合成的替身。"""
import asyncio
import os
import random
import shutil
import subprocess

import numpy as np
import pytest

import eleven_tts
import voice_mix as V

needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="没有 ffmpeg")


def _gen(path_or_pipe, src, secs, fmt=None):
    cmd = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"{src}:duration={secs}",
           "-ac", "1", "-ar", "48000"]
    if fmt == "ogg":
        cmd += ["-c:a", "libopus", "-f", "ogg", "pipe:1"]
        return subprocess.run(cmd, capture_output=True, check=True).stdout
    subprocess.run(cmd + ["-y", path_or_pipe], capture_output=True, check=True)


def _voice(secs=1.0):
    return _gen(None, "sine=frequency=220", secs, fmt="ogg")


@pytest.fixture
def assets(tmp_path, monkeypatch):
    kiss = tmp_path / "kissbox"
    for kind in ("light", "deep"):
        (kiss / kind).mkdir(parents=True)
        for i in range(3):
            _gen(str(kiss / kind / f"{i}.wav"), "anoisesrc=color=white:amplitude=0.3", 0.3)
    water = tmp_path / "water"
    water.mkdir()
    _gen(str(water / "w.wav"), "anoisesrc=color=pink:amplitude=0.5", 5)
    monkeypatch.setattr(V, "KISS_DIR", str(kiss))
    monkeypatch.setattr(V, "WATER_DIR", str(water))
    V._recent_kisses.clear()
    return tmp_path


@pytest.fixture
def fake_synth(monkeypatch):
    calls = []

    async def synth(text, singing=None, previous_text="", next_text="", **k):
        calls.append({"text": text, "prev": previous_text, "next": next_text})
        return _voice(1.0)

    monkeypatch.setattr(eleven_tts, "synth", synth)
    return calls


def _secs(ogg: bytes) -> float:
    return len(V.decode(ogg)) / V.SR


# ── 解析 ──

def test_plan_splits_kisses_and_carries_wet_until_a_tag_without_it():
    reply = ("[low, close] Come here.\n"
             "[kiss]\n"
             "[soft, wet] Don't move.\n"
             "Easy.\n"
             "[kiss deep]\n"
             "[low] Good girl.")
    p = V.plan(reply)
    assert [s["kind"] for s in p] == ["say", "kiss", "say", "kiss", "say"]
    assert p[1]["type"] == "light" and p[3]["type"] == "deep"
    assert [s.get("wet") for s in p if s["kind"] == "say"] == [False, True, False]
    assert "wet" not in p[2]["text"] and "[soft]" in p[2]["text"], "wet 摘掉，嗓子看不到"


def test_strip_wet_and_markers():
    assert V.strip_wet("[low, close, wet] a") == "[low, close] a"
    assert V.strip_wet("[wet] a") == " a"
    assert V.strip_wet("[low and wet] a") == "[low] a"
    assert V.strip_wet("[low] wet hair") == "[low] wet hair", "台词里的 wet 不动，只动标签"
    assert V.strip_markers("a\n[kiss]\n[soft, wet] b") == "a\n[soft] b"
    assert V.strip_markers("a‖[kiss]‖b") == "a‖b", "‖ 是分条记号，不许动"
    # 字幕里也不许出现亲吻行
    assert eleven_tts.strip_tags(V.strip_markers("Come.\n[kiss deep]\nStay.")) == "Come.\nStay."


# ── 拼接（真跑 ffmpeg）──

@needs_ffmpeg
def test_kisses_come_from_the_box_and_speech_gets_context(assets, fake_synth):
    out = asyncio.run(V.render("[low] Come here.\n[kiss]\n[soft] Stay.", rng=random.Random(1)))
    assert out[:4] == b"OggS"
    assert [c["text"] for c in fake_synth] == ["[low] Come here.", "[soft] Stay."], \
        "亲吻那块不送去合成"
    assert fake_synth[0]["next"] == "[soft] Stay." and fake_synth[1]["prev"] == "[low] Come here."
    # 两段 1 秒人声放慢到 0.93 倍 + 一口 0.3 秒亲吻 + 小停顿
    assert 2.4 < _secs(out) < 2.9, _secs(out)
    assert len(V._recent_kisses) == 1


@needs_ffmpeg
def test_water_sits_12db_under_the_voice_and_rings_on_one_second(assets, fake_synth, monkeypatch):
    monkeypatch.setattr(V, "TEMPO", 1.0)
    out = asyncio.run(V.render("[low, close, wet] Easy.", rng=random.Random(2)))
    x = V.decode(out)
    voice_len = int(1.0 * V.SR)
    assert len(x) / V.SR > 1.9, "话说完水声要再留一秒，不能掐断"
    tail = x[voice_len + int(0.1 * V.SR): voice_len + int(0.5 * V.SR)]
    assert V.peak_db(tail) > -40, "尾巴上得有水声"
    voice_peak = V.peak_db(V.decode(_voice(1.0)))
    gap = voice_peak - V.peak_db(tail)
    assert 9 < gap < 20, f"水声的峰要在人声峰下面 12 dB 左右，现在差 {gap:.1f} dB"
    assert "wet" not in fake_synth[0]["text"]


@needs_ffmpeg
def test_kisses_do_not_repeat_until_the_box_runs_out(assets):
    rng = random.Random(3)
    picks = [V.pick_kiss("light", rng) for _ in range(3)]
    assert len(set(picks)) == 3


@needs_ffmpeg
def test_plain_reply_is_one_synth_slowed_a_little(assets, fake_synth):
    out = asyncio.run(V.render("[low] Come here. Stay with me."))
    assert len(fake_synth) == 1, "没有亲吻、没有水声：整段一口气念"
    assert 1.03 < _secs(out) < 1.12, f"慢一点点（0.93 倍）：{_secs(out)}"


@needs_ffmpeg
def test_singing_keeps_its_tempo(assets, fake_synth):
    out = asyncio.run(V.render("[sings] la la la"))
    assert 0.95 < _secs(out) < 1.05


def test_no_box_no_water_no_ffmpeg_falls_back_to_one_plain_synth(fake_synth, monkeypatch, tmp_path):
    monkeypatch.setattr(V, "have_ffmpeg", lambda: False)
    monkeypatch.setattr(V, "KISS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path / "none"))
    out = asyncio.run(V.render("[low] Come here.\n[kiss]\n[soft, wet] Stay."))
    assert out[:4] == b"OggS"
    assert [c["text"] for c in fake_synth] == ["[low] Come here.\n[soft] Stay."], \
        "宁可没有亲吻声，也不能让她收不到语音；标记要去干净"


def test_slowdown_failure_still_sends_the_voice(fake_synth, monkeypatch, tmp_path):
    monkeypatch.setattr(V, "KISS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path / "none"))
    def boom(a):
        raise RuntimeError("ffmpeg 炸了")
    monkeypatch.setattr(V, "slow_down", boom)
    monkeypatch.setattr(V, "have_ffmpeg", lambda: True)
    out = asyncio.run(V.render("Come here."))
    assert out[:4] == b"OggS"
