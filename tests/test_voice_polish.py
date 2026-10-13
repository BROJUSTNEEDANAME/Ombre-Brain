"""贴气、稳口（参考文第三篇第三、五节）：用合成的「话＋气」真跑。"""
import numpy as np

import voice_polish as P

SR = 48000


def voiced(sec, amp=0.08):
    t = np.arange(int(sec * SR)) / SR
    return (amp * (np.sin(2 * np.pi * 120 * t) + 0.5 * np.sin(2 * np.pi * 240 * t))).astype(np.float32)


def breath(sec, amp=0.02, seed=1):
    return (amp * np.random.default_rng(seed).standard_normal(int(sec * SR))).astype(np.float32)


def sil(sec):
    return np.zeros(int(sec * SR), np.float32)


def test_breath_is_moved_next_to_the_words_without_changing_length_or_words():
    x = np.concatenate([voiced(1.0), sil(0.6), breath(0.35), sil(0.5), voiced(1.0)])
    y = P.hug(x, SR)
    assert len(y) == len(x), "总长一毫秒不变"
    f0, rms = P.analyze(y, SR)
    m = P.blocks(f0, rms)
    assert len(m) == 2, "气贴上了话，并进了同一块"
    # 第二段话本身一个采样没动
    words_at = len(x) - int(1.0 * SR)
    assert np.array_equal(y[words_at:], x[words_at:])
    # 气的尾巴到话：从 0.5 秒变成 0.1 秒以内
    f0x, rmsx = P.analyze(x, SR)
    gap_before = (P.blocks(f0x, rmsx)[2][0] - P.blocks(f0x, rmsx)[1][1]) * P.HOP
    assert gap_before > 0.4


def test_breath_right_after_words_is_left_alone():
    """前头空不到 0.3 秒的气是贴着话尾巴的出气，本来就贴着，不动。"""
    x = np.concatenate([voiced(1.0), sil(0.15), breath(0.35), sil(0.6), voiced(1.0)])
    assert np.array_equal(P.hug(x, SR), x)


def test_calm_presses_down_the_block_that_jumps_out():
    parts = [voiced(0.8), sil(0.4), voiced(0.8), sil(0.4), voiced(0.8, amp=0.2), sil(0.4), voiced(0.8), sil(0.4), voiced(0.8)]
    x = np.concatenate(parts)
    y = P.calm(x, SR)
    def lvl(z, a, b):
        seg = z[int(a * SR):int(b * SR)]
        return 20 * np.log10(np.sqrt(np.mean(seg ** 2)))
    loud_before = lvl(x, 2.5, 3.0) - lvl(x, 0.2, 0.7)
    loud_after = lvl(y, 2.5, 3.0) - lvl(y, 0.2, 0.7)
    assert loud_before > 7 and loud_before - loud_after > 5, (loud_before, loud_after)
    assert abs(lvl(y, 0.2, 0.7) - lvl(x, 0.2, 0.7)) < 0.01, "不冒头的一点不动"
    assert len(y) == len(x)


def test_polish_never_changes_length():
    x = np.concatenate([breath(0.3), sil(0.5), voiced(1.2), sil(0.7), breath(0.3, seed=2), sil(0.4), voiced(0.9)])
    assert len(P.polish(x, SR)) == len(x)
