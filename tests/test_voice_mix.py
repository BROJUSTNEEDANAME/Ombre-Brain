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
            _gen(str(kiss / kind / f"{i}.wav"), "anoisesrc=color=white:amplitude=0.3:seed=1", 0.3)
    water = tmp_path / "water"
    water.mkdir()
    _gen(str(water / "w.wav"), "anoisesrc=color=pink:amplitude=0.5:seed=2", 5)
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
    assert [s.get("wet") for s in p if s["kind"] == "say"] == [False, "wet", False]
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


# ── 双语字幕：「译：」行不念，只进字幕 ──

def test_translation_lines_are_dropped_for_the_voice_and_shown_for_her():
    reply = "[low] Come here.\n译：过来。\nDon't move.\n翻译: 别动。‖Stay.‖译：留下。"
    assert V.drop_translations(reply) == "[low] Come here.\nDon't move.\nStay."
    assert V.show_translations(reply) == "[low] Come here.\n过来。\nDon't move.\n别动。‖Stay.‖留下。"
    # 台词里提到「译」字但不是译文行，不动
    assert V.drop_translations("他说译：不对") == "他说译：不对"


def test_render_never_sends_the_translation_to_the_voice(fake_synth, monkeypatch, tmp_path):
    monkeypatch.setattr(V, "KISS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path / "none"))
    asyncio.run(V.render("[low] Come here.\n译：过来。"))
    assert [c["text"] for c in fake_synth] == ["[low] Come here."]


# ── 她手动定的声口（/tone）──

def test_parse_tone_maps_chinese_and_keeps_english():
    assert V.parse_tone("慵懒 低沉") == ("lazy, relaxed, low", [])
    assert V.parse_tone("relaxed, Warm") == ("relaxed, warm", [])
    assert V.parse_tone("温柔、乱写") == ("tender", ["乱写"])


def test_apply_tone_replaces_his_opening_tag_but_keeps_wet():
    assert V.apply_tone("[soft] Come here.\n[low] Stay.", "lazy") == "[lazy] Come here.\n[low] Stay."
    assert V.apply_tone("Come here.", "lazy") == "[lazy] Come here."
    assert V.apply_tone("[kiss]\n译：亲\n[low, wet] Easy.", "tender") == "[kiss]\n译：亲\n[tender, wet] Easy."
    assert V.apply_tone("[soft] a", "") == "[soft] a"


@needs_ffmpeg
def test_speed_can_go_faster_and_one_means_untouched(assets, fake_synth, monkeypatch):
    monkeypatch.setattr(V, "TEMPO", 1.1)
    assert 0.85 < _secs(asyncio.run(V.render("Come here."))) < 0.95
    called = []
    monkeypatch.setattr(V, "TEMPO", 1.0)
    monkeypatch.setattr(V, "slow_down", lambda a: called.append(1) or a)
    asyncio.run(V.render("Come here."))
    assert called == [], "原速就别转码一遍"



# ── 内置合成水声（她说「你自己找一段水声自己合成」）──

def test_builtin_water_is_liquid_band_and_stable():
    import water_synth as W
    for kind, secs in (("wet", 20), ("squish", 8)):
        x = W.get(kind)
        assert abs(len(x) / W.SR - secs) < 0.01
        X = np.abs(np.fft.rfft(x)) ** 2
        f = np.fft.rfftfreq(len(x), 1 / W.SR)
        mid = X[(f >= 200) & (f < 2000)].sum() / X.sum()
        assert mid > 0.7, f"{kind}: 液体声该集中在 200–2000 Hz，现在 {mid:.0%}"
        assert 0.5 < float(np.abs(x).max()) <= 0.71
    assert W.get("wet") is W.get("wet"), "同一进程只算一次"


def test_squish_tag_picks_the_sticky_one_and_is_never_spoken():
    p = V.plan("[soft, squish] Easy.\n[low, wet] Slow.\nStay.\n[low] Done.")
    assert [s["wet"] for s in p] == ["squish", "wet", False], "Stay. 没标签，跟 Slow. 同一段"
    assert all("squish" not in s["text"] and "wet" not in s["text"] for s in p)
    assert V.apply_tone("[soft, squish] Easy.", "tender") == "[tender, squish] Easy."


@needs_ffmpeg
def test_no_upload_uses_builtin_water_by_kind(fake_synth, monkeypatch, tmp_path):
    import water_synth as W
    monkeypatch.setattr(V, "KISS_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path / "none"))
    monkeypatch.setattr(V, "TEMPO", 1.0)
    asked = []
    real = W.get
    monkeypatch.setattr(W, "get", lambda kind="wet": asked.append(kind) or real(kind))
    x = V.decode(asyncio.run(V.render("[low, squish] Easy.", rng=random.Random(5))))
    assert asked == ["squish"]
    assert len(x) / V.SR > 1.9 and V.peak_db(x[int(1.1 * V.SR):int(1.5 * V.SR)]) > -40, "尾巴上得有水声"
    asked.clear()
    monkeypatch.setattr(V, "WATER_BUILTIN", False)
    x = V.decode(asyncio.run(V.render("[low, wet] Easy.", rng=random.Random(5))))
    assert asked == [] and len(x) / V.SR < 1.2, "内置关了、又没上传：不垫水声"


def test_uploaded_water_wins_over_builtin(monkeypatch, tmp_path):
    (tmp_path / "w.wav").write_bytes(b"x")
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path))
    assert V.water_sources() == [str(tmp_path / "w.wav")]
    monkeypatch.setattr(V, "WATER_DIR", str(tmp_path / "none"))
    assert V.water_sources() == ["builtin"]
