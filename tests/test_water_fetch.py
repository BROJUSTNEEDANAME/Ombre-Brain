"""去 freesound 拿真录水声候选：页面解析、授权核对、下载失败要明说。httpx 换成替身真跑。"""
import asyncio
import json
import os
import shutil
import subprocess

import pytest

import water_fetch as F

PAGE_CC0 = """<html><head><title>Freesound - Wet Squishy sound by ricexzeeb</title>
<meta property="og:audio" content="https://cdn.freesound.org/previews/151/151184_2398403-lq.mp3"/>
</head><body><div data-mp3="https://cdn.freesound.org/previews/151/151184_2398403-hq.mp3"></div>
<a href="https://creativecommons.org/publicdomain/zero/1.0/">Creative Commons 0</a></body></html>"""
PAGE_BY = PAGE_CC0.replace("publicdomain/zero/1.0", "licenses/by/4.0").replace("151184", "235335")
PAGE_NOLIC = PAGE_CC0.replace("publicdomain/zero/1.0", "example.com")


def test_parse_page_prefers_hq_and_reads_license():
    info = F.parse_page(PAGE_CC0)
    assert info["preview"].endswith("151184_2398403-hq.mp3")
    assert info["license"] == "CC0" and info["title"] == "Wet Squishy sound by ricexzeeb"
    assert F.parse_page(PAGE_BY)["license"] == "CC BY 4.0"
    assert F.parse_page(PAGE_NOLIC)["license"] == ""
    assert F.parse_page("")["preview"] == ""


def _mp3():
    if not shutil.which("ffmpeg"):
        pytest.skip("没有 ffmpeg")
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                           "anoisesrc=color=pink:amplitude=0.4:seed=3:duration=4",
                           "-ac", "1", "-f", "mp3", "pipe:1"], capture_output=True, check=True).stdout


class _R:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content


def fake_client(routes):
    class C:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def get(self, url):
            for key, resp in routes.items():
                if key in url:
                    return resp
            return _R(404)
    return C


def test_fetch_downloads_only_what_it_can_verify(tmp_path, monkeypatch):
    mp3 = _mp3()
    routes = {
        "/sounds/1/": _R(text=PAGE_CC0),
        "/sounds/2/": _R(text=PAGE_NOLIC),
        "/sounds/3/": _R(503),
        "151184_2398403-hq.mp3": _R(content=mp3),
    }
    monkeypatch.setattr(F.httpx, "AsyncClient", fake_client(routes))
    cands = [("https://freesound.org/people/a/sounds/1/", "一"),
             ("https://freesound.org/people/b/sounds/2/", "二"),
             ("https://freesound.org/people/c/sounds/3/", "三")]
    res = asyncio.run(F.fetch_candidates(str(tmp_path), cands))
    assert [r["ok"] for r in res] == [True, False, False]
    assert os.path.getsize(res[0]["file"]) == len(mp3) and res[0]["license"] == "CC0"
    assert "认不出授权" in res[1]["why"] and res[1]["file"] == ""
    assert "HTTP 503" in res[2]["why"]
    assert not os.path.exists(tmp_path / "02.mp3"), "认不出授权的不许下"
    assert json.load(open(tmp_path / "manifest.json", encoding="utf-8")) == res
    assert F.load_manifest(str(tmp_path)) == res


def test_tiny_download_is_not_counted_as_found(tmp_path, monkeypatch):
    routes = {"/sounds/1/": _R(text=PAGE_CC0), "151184_2398403-hq.mp3": _R(content=b"x" * 10)}
    monkeypatch.setattr(F.httpx, "AsyncClient", fake_client(routes))
    res = asyncio.run(F.fetch_candidates(str(tmp_path), [("https://freesound.org/x/sounds/1/", "一")]))
    assert res[0]["ok"] is False and "没下下来" in res[0]["why"], "空壳不许当成「找到了」"
