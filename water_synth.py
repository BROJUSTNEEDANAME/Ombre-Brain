"""内置水声：用程序合成，不靠录音。

由来 2026-10-11：她说「你自己找一段水声自己合成」（部署机和我这边都拿不到 freesound 的素材）。
第一版用的是「液体里的气泡」模型——有音高的「啵」，她一听：「我的妈呀，你这是水声吗，
感觉人都变成人鱼了。我要的是模仿那个时候的声音」。气泡是水下的声音，不是那种时候的湿声。

现在这版没有音高（频谱尖峰度 ~1.5，气泡版 6.7），三层：
- 密集的细碎粘连声：一两毫秒的带通噪声小脉冲，密度跟着每一下的速度走；
- 滑动的摩擦嘶声：带通噪声，速度越快越响；
- 每次换方向那一下的吸附/拍合：十几二十毫秒、闷一点的短脉冲。
三款（她用 /water 款 A｜B｜C 切）：A 黏、咕叽多；B 慢、滑；C 快、拍合明显。
squish（更黏的咕叽）是同一款再密一点、摩擦少一点。
她上传了真录音就优先用真的。像不像只有她的耳朵能判。
"""
from __future__ import annotations

import functools

import numpy as np

SR = 48000
STYLES = {
    "A": dict(period=(0.8, 1.3), crackle=900, friction=0.15, smack=0.6),
    "B": dict(period=(1.3, 2.0), crackle=400, friction=0.35, smack=0.5),
    "C": dict(period=(0.45, 0.7), crackle=700, friction=0.25, smack=1.0),
}
STYLE = "A"


def band_noise(rng, n, lo, hi):
    X = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    m = np.clip((f - lo) / (0.25 * lo + 1), 0, 1) * np.clip((hi - f) / (0.25 * hi + 1), 0, 1)
    return np.fft.irfft(X * m, n).astype(np.float32)

def kernels(rng, k=16):
    out = []
    for _ in range(k):
        n = int(rng.uniform(0.0012, 0.004) * SR)
        tau = rng.uniform(0.15, 0.9) / 1000
        burst = band_noise(rng, n, rng.uniform(500, 1400), rng.uniform(2800, 7000))
        out.append(burst * np.exp(-np.arange(n) / SR / tau).astype(np.float32))
    return out

def stroke_velocity(rng, N, period):
    """每一下来回：速度从 0 到最大再回 0；返回速度曲线和每次换方向的位置。"""
    v = np.zeros(N, np.float32); turns = []
    i = 0
    while i < N:
        half = int(rng.uniform(*period) / 2 * SR)
        j = min(N, i + half)
        v[i:j] = np.sin(np.linspace(0, np.pi, j - i)) * rng.uniform(0.7, 1.0)
        turns.append(i)
        i = j
    return v, turns

def wet(secs, seed, period=(0.9, 1.6), crackle=500, friction=0.25, smack=0.8, base=40):
    rng = np.random.default_rng(seed)
    N = int(secs * SR)
    v, turns = stroke_velocity(rng, N, period)
    # 1) 粘连的细碎声：密度跟着速度走
    lam = base + crackle * v ** 1.5
    pos = np.nonzero(rng.random(N) < lam / SR)[0]
    amp = rng.lognormal(0, 0.6, len(pos)).astype(np.float32) * (0.3 + 0.7 * v[pos])
    ks = kernels(rng)
    which = rng.integers(0, len(ks), len(pos))
    y = np.zeros(N, np.float32)
    for k_i, ker in enumerate(ks):
        imp = np.zeros(N, np.float32)
        sel = which == k_i
        imp[pos[sel]] = amp[sel]
        y += np.convolve(imp, ker)[:N]
    y /= (np.max(np.abs(y)) + 1e-9)
    # 2) 滑动的摩擦嘶声：速度越快越响
    fr = band_noise(rng, N, 400, 3500)
    fr = fr / (np.max(np.abs(fr)) + 1e-9) * (v ** 2) * friction
    # 3) 换方向那一下的吸附/拍合：短、闷一点
    sm = np.zeros(N, np.float32)
    for t in turns[1:]:
        if rng.random() > 0.85:
            continue
        n = int(rng.uniform(0.012, 0.03) * SR)
        b = band_noise(rng, n, 250, rng.uniform(1800, 3200))
        b = b / (np.max(np.abs(b)) + 1e-9) * np.exp(-np.arange(n) / SR / rng.uniform(0.004, 0.01))
        e = min(N, t + n)
        sm[t:e] += (b[: e - t] * smack * rng.uniform(0.5, 1.0)).astype(np.float32)
    out = y * 0.8 + fr + sm
    return _tame(out)


def _tame(x: np.ndarray, crest_db: float = 13.0) -> np.ndarray:
    """柔和限幅：把偶尔特别响的几下削圆。不然峰均比 25–31 dB，垫到人声下面时
    为了峰不盖过人声，整体只能压得很低——她又会听成「没有水声」。"""
    x = x.astype(np.float64)
    for _ in range(3):
        r = np.sqrt(np.mean(x ** 2)) + 1e-12
        t = r * 10 ** (crest_db / 20)
        x = t * np.tanh(x / t)
    return (x / (np.max(np.abs(x)) + 1e-12) * 0.7).astype(np.float32)


@functools.lru_cache(maxsize=12)
def _make(kind: str, style: str) -> np.ndarray:
    p = dict(STYLES[style])
    if kind == "squish":
        p.update(crackle=int(p["crackle"] * 1.4), friction=p["friction"] * 0.7)
        return wet(8, 11, **p)
    return wet(20, 7, **p)


def get(kind: str = "wet", style: str | None = None) -> np.ndarray:
    """kind="wet"：20 秒；kind="squish"：更黏的 8 秒。style 不给就用她选的那款。同款只算一次。"""
    style = style if style in STYLES else STYLE
    return _make("squish" if kind == "squish" else "wet", style)
