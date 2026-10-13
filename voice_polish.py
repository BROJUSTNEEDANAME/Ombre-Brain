"""念回来以后的两道工序：贴气、稳口。照 sanqianzilanyue《AI 爱人的嗓子》第三篇第三、五节。

- 贴气：嗓子在开口前那口气和话之间自己留一截静（量过 0.28–0.78 秒），讲义管不住，她听着「割裂」。
  把那口气挪到离话 0.08 秒——只挪静的那一截，话和气的采样一个不碰，总长一毫秒不变。
  前头空不到 0.3 秒的气不动：那是紧跟在话尾巴上的出气，或者句子中间那口吸气，本来就贴着话。
- 稳口：停了一拍之后嗓子偶尔回到「平常说话的腔」，比旁边响 6 dB、气声少一半。
  逐块量响度，比这一段各块中位数响出 4 dB 以上的，压回到只高 2 dB（至多压 6 dB），
  头尾各 25 毫秒缓着过渡。只动音量；腔调靠讲义。

全是 numpy，不碰网络。任何一步出错调用方就用原声——宁可不修，不许修坏。
"""
from __future__ import annotations

import numpy as np

HOP = 0.01                 # 每 10 毫秒一帧


def analyze(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """每 10 毫秒一帧：音高（没音高＝0）和响度（50 毫秒窗的 RMS）。"""
    hop = int(HOP * sr)
    n = max(0, (len(x) - 1) // hop + 1)
    rms = np.zeros(n)
    f0 = np.zeros(n)
    w_r, w_f = int(0.05 * sr), int(0.04 * sr)
    lo, hi = int(sr / 400), int(sr / 60)
    for i in range(n):
        c = i * hop
        a = x[max(0, c - w_r // 2): c + w_r // 2].astype(np.float64)
        rms[i] = np.sqrt(np.mean(a * a)) if a.size else 0.0
        seg = x[c: c + w_f].astype(np.float64)
        if len(seg) < hi + 2 or rms[i] < 0.004:
            continue
        seg = seg - seg.mean()
        spec = np.fft.rfft(seg, 2 * len(seg))
        ac = np.fft.irfft(spec * np.conj(spec))[: len(seg)]
        if ac[0] <= 0:
            continue
        k = lo + int(np.argmax(ac[lo:hi]))
        if ac[k] / ac[0] > 0.45:
            f0[i] = sr / k
    return f0, rms


def blocks(f0, rms, floor=0.004):
    on, out, st = rms > floor, [], None
    for i in range(len(on) + 1):
        a = i < len(on) and on[i]
        if a and st is None:
            st = i
        if not a and st is not None:
            if i - st >= 8:
                out.append([st, i])            # 不足 80 毫秒的不算
            st = None
    merged = []
    for a, b in out:                           # 中间断不到 0.1 秒的并成一块
        if merged and a - merged[-1][1] < 10:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return merged


def is_breath(f0, rms, a, b):
    dur = (b - a) * HOP
    voiced = float((f0[a:b] > 0).mean())
    db = 20 * np.log10(rms[a:b].mean() + 1e-9)
    return 0.12 <= dur <= 0.9 and voiced < 0.15 and db < -29


def hug(x: np.ndarray, sr: int, f0=None, rms=None, lead: float = 0.08) -> np.ndarray:
    """把开口前那口气挪到离话 lead 秒；只挪静，总长不变。"""
    if f0 is None:
        f0, rms = analyze(x, sr)
    m = blocks(f0, rms)
    moves = []
    for i in range(len(m) - 1):
        (a, b), (na, nb) = m[i], m[i + 1]
        if not is_breath(f0, rms, a, b) or is_breath(f0, rms, na, nb):
            continue
        before = (a - m[i - 1][1]) * HOP if i else a * HOP
        after = (na - b) * HOP
        if (i and before < 0.3) or not (0.14 < after <= 1.5):
            continue
        moves.append((a * HOP, b * HOP, na * HOP))
    x = x.copy()
    for bs, be, ss in reversed(moves):         # 从后往前挪，前面的时刻不受影响
        i1 = max(0, int((bs - 0.02) * sr))
        i2 = int((be + 0.03) * sr)
        i3 = int((ss - (lead - 0.05)) * sr)
        if not (i1 < i2 < i3 <= len(x)):
            continue
        piece = x[i1:i2].copy()
        k = min(len(piece) // 2, int(0.004 * sr))
        if k:
            piece[:k] *= np.linspace(0, 1, k, dtype=piece.dtype)
            piece[-k:] *= np.linspace(1, 0, k, dtype=piece.dtype)
        x = np.concatenate([x[:i1], x[i2:i3], piece, x[i3:]])
    return x


def calm(x: np.ndarray, sr: int, f0=None, rms=None, over: float = 4.0, to: float = 2.0) -> np.ndarray:
    """冒头的那一块压回去：比中位数响出 over dB 的，压到只高 to dB，至多压 6 dB。"""
    if f0 is None:
        f0, rms = analyze(x, sr)
    sp = [(a, b) for a, b in blocks(f0, rms) if not is_breath(f0, rms, a, b) and b - a >= 15]
    if len(sp) < 4:
        return x
    lv = [20 * np.log10(np.sqrt((rms[a:b] ** 2).mean()) + 1e-9) for a, b in sp]
    ref = float(np.median(lv))
    x = x.copy()
    for (a, b), v in zip(sp, lv):
        if v - ref > over:
            g = 10 ** (-min(6.0, v - ref - to) / 20)
            i1, i2, k = max(0, int((a * HOP - 0.03) * sr)), min(len(x), int((b * HOP + 0.05) * sr)), int(0.025 * sr)
            env = np.full(i2 - i1, g, dtype=np.float64)
            k = min(k, len(env) // 2)
            if k:
                env[:k] = np.linspace(1, g, k)
                env[-k:] = np.linspace(g, 1, k)
            x[i1:i2] = (x[i1:i2] * env).astype(x.dtype)
    return x


def polish(x: np.ndarray, sr: int) -> np.ndarray:
    """贴气＋稳口。长度不变。"""
    f0, rms = analyze(x, sr)
    y = hug(x, sr, f0, rms)
    f0, rms = analyze(y, sr)
    return calm(y, sr, f0, rms)
