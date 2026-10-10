"""做亲盒：静音切片、湿度、转录筛。整条 build 用替身真跑一遍（合成和转录换成假的）。"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess

import numpy as np
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("make_kissbox", os.path.join(_ROOT, "scripts", "make-kissbox.py"))
K = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(K)
SR = K.ANALYSIS_SR
needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="没有 ffmpeg")


def _sil(s):
    return np.zeros(int(s * SR), dtype=np.float32)


def _speech(s):
    t = np.arange(int(s * SR)) / SR
    return (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)   # 低频，像念字：很「干」


def _kiss(s, seed):
    return (0.3 * np.random.default_rng(seed).standard_normal(int(s * SR))).astype(np.float32)


def _take():
    """说话 0.5–1.3 s，亲 1.8–2.1 s，说话 2.6–3.0 s，亲 3.5–4.2 s，哼 4.8–5.3 s。"""
    parts = [_sil(.5), _speech(.8), _sil(.5), _kiss(.3, 1), _sil(.5), _speech(.4),
             _sil(.5), _kiss(.7, 2), _sil(.6), _speech(.5), _sil(.5)]
    return np.concatenate(parts)


SPOKEN = [(0.5, 1.3), (2.6, 3.0)]        # 转录认出来的字的位置（哼那段没认出字）


def test_wetness_separates_kisses_from_humming():
    assert K.wetness(_kiss(.3, 0)) > 1.0
    assert K.wetness(_speech(.3)) < 0.01
    assert K.wetness(_sil(.1)) == 0.0


def test_regions_follow_the_sound():
    r = K.find_regions(_take(), SR)
    assert len(r) == 5, r
    assert abs(r[1][0] - 1.8) < 0.03 and abs(r[1][1] - 2.1) < 0.03


def test_only_real_kisses_survive_both_filters():
    kept, dropped = K.pick_clips(_take(), SR, SPOKEN, 0.2)
    assert [c["type"] for c in kept] == ["light", "deep"], kept
    assert dropped["念成字"] == 2 and dropped["太干"] == 1, dropped


@needs_ffmpeg
def test_build_swaps_in_a_new_box_only_when_it_is_good(tmp_path, monkeypatch):
    import eleven_tts
    take = np.concatenate([_take(), _take()])
    ogg = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                          "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                         input=take.tobytes(), capture_output=True, check=True).stdout
    posted = []

    async def fake_post(text, model, singing, timeout, ctx=None):
        posted.append(text)
        return ogg

    async def fake_words(audio):
        return SPOKEN + [(a + 5.8, b + 5.8) for a, b in SPOKEN]

    monkeypatch.setattr(eleven_tts, "_post", fake_post)
    monkeypatch.setattr(eleven_tts, "configured", lambda: True)
    monkeypatch.setattr(eleven_tts, "STABILITY", eleven_tts.STABILITY)   # 跑完还原，别污染别的测试
    monkeypatch.setattr(K, "transcribe_words", fake_words)
    box = str(tmp_path / "kissbox")
    os.makedirs(box + "/light"); open(box + "/light/old.wav", "wb").close()

    assert asyncio.run(K.build(3, 0.2, box)) == 0
    assert len(posted) == 3 and "kisses" in posted[0]
    man = json.load(open(box + "/manifest.json", encoding="utf-8"))
    assert len(man["clips"]) >= 6 and man["dropped"]["念成字"] >= 6
    assert not os.path.exists(box + "/light/old.wav"), "新盒子换上了"
    assert os.path.exists(str(tmp_path / "kissbox.old" / "light" / "old.wav")), "旧的留一份"
    assert eleven_tts.STABILITY == 0.35


@needs_ffmpeg
def test_no_transcription_means_no_box_not_an_unfiltered_one(tmp_path, monkeypatch, capsys):
    import eleven_tts
    async def fake_post(text, model, singing, timeout, ctx=None):
        return b"OggS"
    async def no_words(audio):
        raise RuntimeError("HTTP 401 missing_permissions")
    monkeypatch.setattr(eleven_tts, "_post", fake_post)
    monkeypatch.setattr(eleven_tts, "configured", lambda: True)
    monkeypatch.setattr(eleven_tts, "STABILITY", eleven_tts.STABILITY)   # 跑完还原，别污染别的测试
    monkeypatch.setattr(K, "transcribe_words", no_words)
    box = str(tmp_path / "kissbox")
    assert asyncio.run(K.build(2, 0.2, box)) == 1
    assert not os.path.exists(box) and not os.path.exists(box + ".new")
    out = capsys.readouterr().out
    assert "❌" in out and "Speech to Text" in out and "✅" not in out


@needs_ffmpeg
def test_too_few_clips_keeps_the_old_box(tmp_path, monkeypatch, capsys):
    import eleven_tts
    ogg = subprocess.run(["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1",
                          "-i", "pipe:0", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                         input=_speech(2).tobytes(), capture_output=True, check=True).stdout
    async def fake_post(text, model, singing, timeout, ctx=None):
        return ogg
    async def words(audio):
        return [(0.0, 2.0)]
    monkeypatch.setattr(eleven_tts, "_post", fake_post)
    monkeypatch.setattr(eleven_tts, "configured", lambda: True)
    monkeypatch.setattr(eleven_tts, "STABILITY", eleven_tts.STABILITY)   # 跑完还原，别污染别的测试
    monkeypatch.setattr(K, "transcribe_words", words)
    box = str(tmp_path / "kissbox")
    os.makedirs(box + "/deep"); open(box + "/deep/keep.wav", "wb").close()
    assert asyncio.run(K.build(2, 0.2, box)) == 1
    assert os.path.exists(box + "/deep/keep.wav")
    assert "没换掉原来的亲盒" in capsys.readouterr().out
