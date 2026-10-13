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

def test_plan_splits_kisses_and_carries_wet_until_changed():
    reply = ("[low, close] Come here.\n"
             "[kiss]\n"
             "[soft, wet] Don't move.\n"
             "Easy.\n"
             "[kiss deep]\n"
             "[low] Good girl.")
    p = V.plan(reply)
    assert [s["kind"] for s in p] == ["say", "kiss", "say", "kiss", "say"]
    assert p[1]["type"] == "light" and p[3]["type"] == "deep"
    assert [s.get("wet") for s in p if s["kind"] == "say"] == [False, "wet", "wet"], \
        "参考文第二篇：没写记号的标签不让水声退掉，写 still 才停"
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
def test_water_rings_on_after_the_voice(assets, fake_synth, monkeypatch):
    monkeypatch.setattr(V, "TEMPO", 1.0)
    out = asyncio.run(V.render("[low, close, wet] Easy.", rng=random.Random(2)))
    x = V.decode(out)
    voice_len = int(1.0 * V.SR)
    assert len(x) / V.SR > 1.9, "话说完水声要再留一秒，不能掐断"
    assert V.peak_db(x[voice_len + int(0.1 * V.SR): voice_len + int(0.5 * V.SR)]) > -45, "尾巴上得有水声"
    assert "wet" not in fake_synth[0]["text"]


def test_water_layer_sits_under_the_voice_by_average_loudness():
    """她：「语音没有水声」。按峰对齐时，内置气泡声的平均响度比人声低了 20 dB，听不见。
    现在按平均响度对齐（低 WATER_GAP_DB），峰再兜底：永远比人声峰低至少 WATER_PEAK_ROOM_DB。"""
    import water_synth as W
    t = np.arange(int(3 * V.SR)) / V.SR
    # 像说话的替身：有起伏、有停顿的低频音
    voice = (0.4 * np.sin(2 * np.pi * 140 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 1.3 * t)) ** 2).astype(np.float32)
    for kind in ("wet", "squish"):
        mix = V.lay_water(voice, W.get(kind), random.Random(1))
        water = mix - np.concatenate([voice, np.zeros(len(mix) - len(voice), np.float32)])
        body = water[int(0.5 * V.SR): len(voice) - int(0.4 * V.SR)]
        gap = V.rms_db(voice) - V.rms_db(body)
        assert V.peak_db(water) <= V.peak_db(voice) - V.WATER_PEAK_ROOM_DB + 0.01, "水声绝不盖过人声"
        assert gap < 15, f"{kind}：水声比人声低了 {gap:.1f} dB，又会听不见"
        assert gap > V.WATER_GAP_DB - 1.5, f"{kind}：水声太响了（只低 {gap:.1f} dB）"


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
    monkeypatch.setattr(V, "TEMPO", 1.0)
    assert 0.95 < _secs(asyncio.run(V.render("Come here."))) < 1.05, "原速：贴气稳口照做，长度不变"



# ── 内置合成水声（她说「你自己找一段水声自己合成」）──

def _spikiness(x):
    """频谱尖峰度：有音高的声音（气泡那种「啵」）很高，摩擦/粘连这种噪声类的很低。"""
    X = np.abs(np.fft.rfft(x))
    f = np.fft.rfftfreq(len(x), 1 / V.SR)
    sm = np.convolve(X, np.ones(50) / 50, "same")
    band = (f > 200) & (f < 4000)
    return float(np.max(sm[band]) / np.median(sm[band]))


def test_builtin_water_has_no_pitch_so_it_is_not_underwater():
    """她听第一版：「感觉人都变成人鱼了，我要的是模仿那个时候的声音」。
    气泡模型有音高（尖峰度 6.7），像水下。现在三款都必须是没有音高的湿摩擦声。"""
    import water_synth as W
    for style in W.STYLES:
        for kind, secs in (("wet", 20), ("squish", 8)):
            x = W.get(kind, style)
            assert abs(len(x) / W.SR - secs) < 0.01
            assert _spikiness(x) < 3, f"{style}/{kind} 有音高，又成人鱼了：{_spikiness(x):.1f}"
            X = np.abs(np.fft.rfft(x)) ** 2
            f = np.fft.rfftfreq(len(x), 1 / W.SR)
            assert X[(f >= 500) & (f < 8000)].sum() / X.sum() > 0.8
            assert 0.5 < float(np.abs(x).max()) <= 0.71
    assert W.get("wet") is W.get("wet"), "同一进程同一款只算一次"


def test_squish_tag_picks_the_sticky_one_and_is_never_spoken():
    p = V.plan("[soft, squish] Easy.\n[low, wet] Slow.\nStay.\n[low] Done.")
    assert [s["wet"] for s in p] == ["squish", "wet"], "换种类才切段；没记号的接着垫"
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


# ── 念完先转录对账（参考文第三篇第十节）──

def test_echo_bad_catches_repeats_and_swallows_but_not_fillers():
    E = eleven_tts
    assert E.echo_bad("Come here I missed you come here I missed you so much",
                      "Come here. I missed you so much.")[0] >= 3, "念重了"
    assert E.echo_bad("Come here.", "Come here. I missed you so much. Stay with me tonight.")[0] >= 3, "吞了"
    assert E.echo_bad("Mm, come here. I missed you so much.",
                      "[low] Mm… come here. [exhales] I missed you so much.")[0] == 0, "语气词和标签不算"
    assert E.echo_bad("过来过来过来", "过来。")[0] == 0, "稿子太短不判"


def test_mangled_take_is_redone_once_and_the_cleaner_one_kept(monkeypatch, tmp_path):
    takes, heard = [], iter(["come here I missed you come here I missed you so much",
                             "come here I missed you so much"])

    async def synth(text, singing=None, **k):
        takes.append(text)
        return b"OggS" + bytes([len(takes)]) * 300

    async def transcribe(audio):
        return next(heard)

    monkeypatch.setattr(eleven_tts, "synth", synth)
    monkeypatch.setattr(eleven_tts, "transcribe", transcribe)
    V.ECHO_STATS.update(checked=0, redone=0)
    out = asyncio.run(V.synth_checked("Come here. I missed you so much."))
    assert len(takes) == 2 and out[4] == 2, "重念的那版更干净，留它"
    assert V.ECHO_STATS == {"checked": 1, "redone": 1}


def test_no_transcription_means_no_redo_and_singing_is_never_checked(monkeypatch):
    calls = []

    async def synth(text, singing=None, **k):
        calls.append(text)
        return b"OggS" + b"x" * 300

    async def nope(audio):
        return None

    async def boom(audio):
        raise AssertionError("唱歌不该转录")

    monkeypatch.setattr(eleven_tts, "synth", synth)
    monkeypatch.setattr(eleven_tts, "transcribe", nope)
    asyncio.run(V.synth_checked("Come here. I missed you so much. Stay."))
    assert len(calls) == 1
    monkeypatch.setattr(eleven_tts, "transcribe", boom)
    asyncio.run(V.synth_checked("[sings] la la la la la la", singing=True))


# ── 随手一口气（参考文第三篇第十二节）──

def test_daily_lead_is_random_half_bare_and_leaves_bed_and_kisses_alone():
    import collections
    c = collections.Counter(V.daily_lead("[low] Come here.", random.Random(i)) for i in range(400))
    bare = c["[low] Come here."] / 400
    assert 0.4 < bare < 0.75, f"一半左右什么都不垫，现在 {bare:.0%}"
    assert any("[exhales]" in k for k in c) and any("[chuckles softly]" in k for k in c)
    assert V.daily_lead("Come here.", random.Random(1)).startswith(V.DAILY_TAG), "没写声口就补他的口气"
    assert V.daily_lead("[low] [inhales] Come here.", random.Random(3)) == "[low] [inhales] Come here."
    assert V.daily_lead("[low, wet] Easy.", random.Random(3)) == "[low, wet] Easy.", "床上不垫"
    assert V.daily_lead("[kiss]\nStay.", random.Random(3)) == "[kiss]\nStay."


def test_sound_persists_until_changed_or_still():
    """参考文第二篇：垫着的声音一直跟着往下，直到换成另一样、或者明说停。"""
    p = V.plan("[low, close, wet] a\n[soft] b\n[low, still] c\nd")
    assert [(s["wet"], s["text"]) for s in p] == [("wet", "[low, close] a\n[soft] b"), (False, "[low] c\nd")]
    p = V.plan("[fingering] a\n[licking] b\n[spank] c\n[low, fast fingering] d")
    assert [s["wet"] for s in p] == ["squish", "wet", "squish"]
    assert all(w not in s["text"] for s in p for w in ("fingering", "licking", "spank"))
