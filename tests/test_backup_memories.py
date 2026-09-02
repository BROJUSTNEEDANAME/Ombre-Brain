"""备份脚本的真跑测试。

存在的理由：611 条记忆只搬这一次，脚本挂了才发现就晚了。
真实事故：从 VPS 备份 Render 时 /api/buckets 回 403 —— 那些接口只对
127.0.0.1 免密，从别的机器打过去必须带 token，脚本原来根本不会带。
"""
import io
import json
import urllib.error
import urllib.request

import backup_memories as bm


def test_token_is_sent_as_bearer_header(monkeypatch):
    seen = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps([{"id": "a"}]).encode()

    def fake_open(req, timeout=None):
        seen["headers"] = dict(req.headers)
        seen["url"] = req.full_url
        return _R()

    monkeypatch.setattr(bm, "BRAIN_TOKEN", "s3cret")
    monkeypatch.setattr(bm, "BRAIN_URL", "https://brain.test")
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)

    assert bm._get("/api/buckets") == [{"id": "a"}]
    # urllib 会把 header 名首字母大写，所以别拿原样字符串比
    assert seen["headers"]["Authorization"] == "Bearer s3cret"
    assert seen["url"] == "https://brain.test/api/buckets"


def test_no_token_means_no_auth_header(monkeypatch):
    """本机备份不需要 token，别硬塞一个空的 Bearer 上去。"""
    seen = {}

    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"[]"

    def fake_open(req, timeout=None):
        seen["headers"] = dict(req.headers)
        return _R()

    monkeypatch.setattr(bm, "BRAIN_TOKEN", "")
    monkeypatch.setattr(urllib.request, "urlopen", fake_open)
    bm._get("/api/buckets")
    assert "Authorization" not in seen["headers"]


def test_403_tells_her_exactly_what_is_missing(monkeypatch, capsys):
    """403 不能只说「连不上」——她会以为是 Render 睡了，白等半天。"""
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

    monkeypatch.setattr(bm, "BRAIN_TOKEN", "")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    try:
        bm.main()
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("403 必须退出，不许假装成功")
    out = capsys.readouterr().out
    assert "403" in out and "OMBRE_BRAIN_TOKEN" in out and "OMBRE_WEB_TOKEN" in out
