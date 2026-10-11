"""内置水声：用物理模型合成，不靠录音。

由来 2026-10-11：她说「你自己找一段水声自己合成」。参考文（ai-voice-breath-kiss-water）
用的是 freesound 上的 CC0 真录音，但部署机和我这边都拿不到那边的素材，所以先合成一段垫着：
- 液体里的气泡：频率略微上扬的衰减正弦（van den Doel 气泡模型），成团落在每一下动作中段。
- 湿的挤压：带通噪声，中心频率扫过去，一下压进去、一下抽出来。
- 一层很轻的湿底噪，别让间隙死寂。
她用 /water 上传了真录音，就优先用真的；/water 内置 关 可以关掉这一段。
参考文也说过 AI 生成的水声「太干净、一听就是机器做的」——这段是不是够像，只有她的耳朵能判。
"""
from __future__ import annotations

import functools

import numpy as np

SR = 48000
def onepole_lp(x, fc):
    a = np.exp(-2*np.pi*fc/SR); y = np.empty_like(x); s = 0.0
    for i, v in enumerate(x):
        s = (1-a)*v + a*s; y[i] = s
    return y

def bubble(rng, f0, amp):
    """液体里一个气泡：频率略微上扬的衰减正弦（van den Doel 气泡模型）。"""
    d = 0.043*f0 + 0.0014*f0**1.5           # 阻尼
    n = int(min(0.08, 6/d)*SR)
    t = np.arange(n)/SR
    xi = rng.uniform(0.05, 0.25)            # 上扬
    f = f0*(1 + xi*d*t)
    ph = 2*np.pi*np.cumsum(f)/SR
    return (amp*np.sin(ph)*np.exp(-d*t)).astype(np.float32)

def squelch(rng, n, f_lo, f_hi):
    """湿的挤压：带通噪声，中心频率扫过去。"""
    noise = rng.standard_normal(n).astype(np.float32)
    fc = np.linspace(f_lo, f_hi, n)
    # 两级一阶低通减出一个粗带通
    lp = noise.copy(); s1 = s2 = 0.0
    out = np.empty(n, np.float32)
    for i in range(n):
        a = np.exp(-2*np.pi*fc[i]*1.6/SR); b = np.exp(-2*np.pi*fc[i]*0.6/SR)
        s1 = (1-a)*noise[i] + a*s1; s2 = (1-b)*noise[i] + b*s2
        out[i] = s1 - s2
    env = np.sin(np.linspace(0, np.pi, n))**1.5
    return out*env

def strokes(secs, seed, period=(1.1, 1.9), dens=60, f_range=(350, 1400), squish=0.5):
    rng = np.random.default_rng(seed)
    N = int(secs*SR); y = np.zeros(N, np.float32)
    t = 0.0
    while t < secs - 0.3:
        T = rng.uniform(*period)
        a, b = int(t*SR), int(min(secs, t+T)*SR)
        # 一下：前半段压进去、后半段抽出来，各一团挤压声
        half = (b-a)//2
        for k, (lo, hi) in enumerate(((f_range[0], f_range[1]), (f_range[1]*0.8, f_range[0]*1.2))):
            st = a + k*half
            n = int(half*rng.uniform(0.6, 0.9))
            if st+n < N:
                y[st:st+n] += squish*rng.uniform(0.5, 1.0)*squelch(rng, n, lo, hi)
        # 这一下里的气泡：集中在动作中段
        nb = rng.poisson(dens*T)
        for _ in range(nb):
            c = a + int(rng.beta(2, 2)*(b-a))
            f0 = rng.lognormal(np.log(900), 0.45)
            bb = bubble(rng, f0, rng.uniform(0.05, 0.35))
            if c+len(bb) < N:
                y[c:c+len(bb)] += bb
        t += T
    # 一层很轻的湿底噪，别让间隙死寂
    bed = onepole_lp(rng.standard_normal(N).astype(np.float32), 900)*0.02
    y = y + bed
    return (y/np.max(np.abs(y))*0.7).astype(np.float32)


@functools.lru_cache(maxsize=4)
def get(kind: str = "wet") -> np.ndarray:
    """kind="wet"：慢的湿声（20 秒）；kind="squish"：更黏的咕叽（8 秒）。同一进程里只算一次。"""
    if kind == "squish":
        return strokes(8, 11, period=(0.5, 0.9), dens=140, f_range=(250, 900), squish=0.8)
    return strokes(20, 7)
