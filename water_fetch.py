"""去 freesound 拿真录的水声候选，给她一段段听、她挑。

由来 2026-10-13：我合成的水声她听了两版——第一版「人都变成人鱼了」，第二版「好难听」，
「你就不能自己去找那种网站然后模仿里面的用吗」。参考文（ai-voice-breath-kiss-water）
本来用的就是 freesound 上公有领域的真录音；我这边连不上那个网站，但 VPS 连得上，
所以在 VPS 上由桥去下载。我能搜，听不了——所以只下候选，挑哪段由她的耳朵定。

只取页面上公开的试听版（mp3，不用登录），先在页面上核对授权：CC0 直接用；
其它 Creative Commons 也收，但把作者和授权记进清单。页面认不出试听地址或授权的，
就说那一段没拿到——不拿一个空壳冒充「找到了」。
"""
from __future__ import annotations

import json
import os
import re

import httpx

# 我用搜索挑的候选。她听了不像的，可以从这里删、往这里加（页面地址就行）。
CANDIDATES = [
    ("https://freesound.org/people/ricexzeeb/sounds/151184/", "湿软的燕麦被挤压（黏、咕叽）"),
    ("https://freesound.org/people/breadparticles/sounds/575332/", "在碗里挤湿海绵（水多、滑）"),
    ("https://freesound.org/people/apocbot/sounds/235335/", "皮肤摩擦、轻拍（皮肤贴皮肤）"),
    ("https://freesound.org/people/cognito%20perceptu/sounds/103354/", "挤一瓶很稠的液体（稠、黏）"),
    ("https://freesound.org/people/Breviceps/sounds/448080/", "很短的湿「咔哒」（短促）"),
]

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_PREVIEW_RE = re.compile(r"https://cdn\.freesound\.org/previews/\d+/\d+_\d+-(hq|lq)\.mp3")
_LICENSE_RE = re.compile(r"creativecommons\.org/(publicdomain/zero|licenses/[a-z-]+)/(\d(?:\.\d)?)")
_TITLE_RE = re.compile(r"<title>\s*Freesound\s*-\s*(.*?)\s*</title>", re.S | re.I)


def parse_page(html: str) -> dict:
    """从 freesound 的声音页面里认出：试听地址（优先高音质）、授权、标题。认不出的字段是空串。"""
    previews = [m.group(0) for m in _PREVIEW_RE.finditer(html or "")]
    hq = [p for p in previews if p.endswith("-hq.mp3")]
    lic = _LICENSE_RE.search(html or "")
    title = _TITLE_RE.search(html or "")
    if lic:
        kind = "CC0" if lic.group(1).startswith("publicdomain/zero") else \
            "CC " + lic.group(1).split("/", 1)[1].upper() + " " + lic.group(2)
    else:
        kind = ""
    return {"preview": (hq or previews or [""])[0], "license": kind,
            "title": title.group(1).strip() if title else ""}


async def fetch_candidates(out_dir: str, candidates=None, timeout: float = 30.0) -> list[dict]:
    """把候选下载到 out_dir，返回每一段的结果（ok / why）。清单写进 out_dir/manifest.json。"""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                 headers={"User-Agent": UA}) as c:
        for i, (url, note) in enumerate(candidates or CANDIDATES, 1):
            item = {"n": i, "url": url, "note": note, "ok": False, "why": "", "file": "",
                    "license": "", "title": ""}
            try:
                r = await c.get(url)
                if r.status_code != 200:
                    item["why"] = f"页面打不开（HTTP {r.status_code}）"
                    results.append(item)
                    continue
                info = parse_page(r.text)
                item.update(license=info["license"], title=info["title"])
                if not info["license"]:
                    item["why"] = "页面上认不出授权，没下载"
                elif not info["preview"]:
                    item["why"] = "页面上认不出试听地址，没下载"
                else:
                    a = await c.get(info["preview"])
                    if a.status_code != 200 or len(a.content) < 2000:
                        item["why"] = f"试听文件没下下来（HTTP {a.status_code}，{len(a.content)} 字节）"
                    else:
                        path = os.path.join(out_dir, f"{i:02d}.mp3")
                        with open(path, "wb") as fh:
                            fh.write(a.content)
                        item.update(ok=True, file=path)
            except Exception as e:  # noqa: BLE001
                item["why"] = f"出错：{type(e).__name__}: {str(e)[:80]}"
            results.append(item)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    return results


def load_manifest(out_dir: str) -> list[dict]:
    try:
        with open(os.path.join(out_dir, "manifest.json"), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []
