import asyncio

import pytest

import coreading
from coreading import ReadingError, ReadingStore, assert_public_url, extract_article, normalize_url


def sample_article():
    body = "".join(f"<p>这是第{i}段正文，人物正在推进故事，也留下足够长的可读文字。</p>" for i in range(1, 12))
    return f"""
    <html><head><title>测试作品 - 第十二章</title></head><body>
      <nav>首页 目录 广告</nav><div class="advertisement">赌场推广 点击下载</div>
      <main><h1>第十二章 雨夜</h1>{body}</main>
      <a href="/chapter-11">上一章</a><a href="/chapter-13">下一章</a>
      <script>fetch('https://tracker.invalid')</script>
    </body></html>
    """


def test_extract_article_removes_noise_and_keeps_navigation():
    result = extract_article(sample_article(), "https://public.example/chapter-12")
    text = "\n".join(item["text"] for item in result["paragraphs"])
    assert result["title"] == "第十二章 雨夜"
    assert "赌场" not in text
    assert "tracker" not in text
    assert len(result["paragraphs"]) == 11
    assert result["previous_url"] == "https://public.example/chapter-11"
    assert result["next_url"] == "https://public.example/chapter-13"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "http://localhost:3300/",
    "http://service.internal/data",
    "ftp://public.example/book",
])
def test_normalize_url_rejects_non_public_shapes(url):
    with pytest.raises(ReadingError):
        normalize_url(url)


def test_ssrf_rejects_cloud_metadata_ip():
    with pytest.raises(ReadingError):
        asyncio.run(assert_public_url("http://169.254.169.254/latest/meta-data"))


def test_login_failure_never_uses_browser_fallback(monkeypatch):
    calls = []

    async def denied(_url):
        raise ReadingError("这个页面需要登录、验证或拒绝抓取，系统不会绕过。")

    async def browser(_url):
        calls.append("browser")
        return "", ""

    monkeypatch.setattr(coreading, "_fetch_static", denied)
    monkeypatch.setattr(coreading, "_fetch_rendered", browser)
    with pytest.raises(ReadingError):
        asyncio.run(coreading.fetch_article("https://public.example/chapter"))
    assert calls == []


def test_store_cache_progress_annotations_and_anchor_are_idempotent(tmp_path):
    store = ReadingStore(str(tmp_path))
    parsed = extract_article(sample_article(), "https://public.example/chapter-12")
    parsed.update({"source_url": "https://public.example/chapter-12", "extraction": "http"})
    chapter = store.save_import(parsed)

    assert store.find_by_url(parsed["source_url"])["id"] == chapter["id"]
    same_text = dict(parsed, source_url="https://public.example/chapter-12?ref=share")
    assert store.save_import(same_text)["id"] == chapter["id"]
    state = store.set_state(chapter_id=chapter["id"], paragraph_id="p4", selection="人物正在推进故事")
    assert state["paragraph_id"] == "p4"
    assert store.get_state(include_context=True)["selection"] == "人物正在推进故事"

    first = store.add_annotation(chapter["id"], "p4", "这一句", author="闪闪", kind="highlight")
    again = store.add_annotation(chapter["id"], "p4", "这一句", author="闪闪", kind="highlight")
    assert first["id"] == again["id"]
    assert len(store.get_chapter(chapter["id"])["annotations"]) == 1

    store.save_analysis(chapter["id"], {
        "summary": "一段摘要",
        "anchors": [{"paragraph_id": "p4", "hint": "这里真的值得停一下"}],
        "memory": [{"kind": "character", "key": "主角的选择", "value": "决定留下"}],
    })
    assert store.claim_anchor(chapter["id"], "p4")["hint"]
    assert store.claim_anchor(chapter["id"], "p4") is None
    store.upsert_work_memory(chapter["work_id"], [
        {"kind": "character", "key": "主角的选择", "value": "决定离开后又返回"},
    ])
    memories = store.work_memory(chapter["work_id"])
    assert len(memories) == 1
    assert memories[0]["value"] == "决定离开后又返回"
