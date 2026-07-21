"""Private, URL-based co-reading storage and extraction.

Book text never enters logs or the repository. The normal HTTP path is cheap;
an optional Playwright renderer is started only when static extraction fails.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import ipaddress
import json
import os
import re
import socket
import tempfile
import threading
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_RESPONSE_BYTES = int(os.environ.get("OMBRE_READING_MAX_BYTES", str(5 * 1024 * 1024)))
MAX_REDIRECTS = 3
MIN_ARTICLE_CHARS = 240
_HTTP_SEMAPHORE = asyncio.Semaphore(2)
_BROWSER_SEMAPHORE = asyncio.Semaphore(1)


class ReadingError(RuntimeError):
    """Safe error suitable for returning to the private UI."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def normalize_url(value: str) -> str:
    raw = str(value or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise ReadingError("链接格式不对。") from exc
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise ReadingError("只支持公开的 HTTP 或 HTTPS 链接。")
    if parts.username or parts.password:
        raise ReadingError("链接不能包含账号或密码。")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ReadingError("不能读取本机或内网地址。")
    port = parts.port
    netloc = f"{host}:{port}" if port else host
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, parts.query, ""))


async def assert_public_url(value: str) -> str:
    url = normalize_url(value)
    parts = urlsplit(url)
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)),
            timeout=3,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ReadingError("这个网站现在解析不到。") from exc
    addresses = {item[4][0].split("%", 1)[0] for item in infos}
    if not addresses:
        raise ReadingError("这个网站现在解析不到。")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError as exc:
            raise ReadingError("网站地址不安全。") from exc
        if not address.is_global:
            raise ReadingError("不能读取本机、内网或云元数据地址。")
    return url


def _clean_text(value: str) -> str:
    value = str(value or "").replace("\u200b", "").replace("\ufeff", "")
    value = re.sub(r"[\t\r\f\v ]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _safe_chapter_link(base_url: str, href: str) -> str:
    if not href:
        return ""
    try:
        candidate = normalize_url(urljoin(base_url, href))
    except ReadingError:
        return ""
    base = urlsplit(base_url)
    target = urlsplit(candidate)
    # Chapter navigation stays on the same host. This also prevents an imported
    # page from turning its ad links into trusted next-chapter actions.
    return candidate if target.hostname == base.hostname else ""


class _FallbackArticleParser(HTMLParser):
    """Small dependency-free fallback for conventional paragraph pages."""

    BLOCKS = {"p", "blockquote", "li", "h2", "h3"}
    SKIP = {"script", "style", "noscript", "iframe", "svg", "canvas", "form", "nav", "footer", "header", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.skip_tags = []
        self.current = None
        self.buffer = []
        self.paragraphs = []
        self.title = ""
        self.document_title = ""
        self.links = []
        self.link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = str(attrs.get("class") or "").lower()
        noisy = any(word in classes.split() for word in ("ad", "ads", "advert", "advertisement", "popup", "recommend", "related"))
        if tag in self.SKIP or noisy:
            self.skip_depth += 1
            self.skip_tags.append(tag)
            return
        if self.skip_depth:
            return
        if tag in self.BLOCKS or tag in {"h1", "title"}:
            self.current, self.buffer = tag, []
        if tag == "a":
            self.link = {"href": attrs.get("href", ""), "rel": attrs.get("rel", ""), "text": []}

    def handle_endtag(self, tag):
        if self.skip_depth:
            if self.skip_tags and tag == self.skip_tags[-1]:
                self.skip_depth = max(0, self.skip_depth - 1)
                self.skip_tags.pop()
            return
        if self.link and tag == "a":
            self.link["text"] = _clean_text("".join(self.link["text"]))
            self.links.append(self.link)
            self.link = None
        if self.current == tag:
            text = _clean_text("".join(self.buffer))
            if tag == "h1" and text:
                self.title = text
            elif tag == "title" and text:
                self.document_title = text
            elif tag in self.BLOCKS and len(text) >= 8:
                self.paragraphs.append(text)
            self.current, self.buffer = None, []

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.current:
            self.buffer.append(data)
        if self.link:
            self.link["text"].append(data)


def _extract_article_fallback(html: str, source_url: str) -> dict:
    parser = _FallbackArticleParser()
    parser.feed(html)
    unique = []
    seen = set()
    for text in parser.paragraphs:
        if text not in seen:
            seen.add(text)
            unique.append({"id": f"p{len(unique) + 1}", "text": text})
    if sum(len(item["text"]) for item in unique) < MIN_ARTICLE_CHARS:
        raise ReadingError("没有识别出足够的正文。")
    title = parser.title or parser.document_title or "未命名章节"
    work_title = _clean_text(parser.document_title.replace(title, "")) or parser.document_title or title
    previous_url = next_url = ""
    for link in parser.links:
        label = str(link.get("text") or "").lower()
        rel = str(link.get("rel") or "").lower()
        if not previous_url and ("prev" in rel or re.search(r"上一[章节页]|上一章|前一章", label)):
            previous_url = _safe_chapter_link(source_url, link.get("href"))
        if not next_url and ("next" in rel or re.search(r"下一[章节页]|下一章|后一章", label)):
            next_url = _safe_chapter_link(source_url, link.get("href"))
    return {
        "title": title[:180], "work_title": work_title[:180], "paragraphs": unique,
        "previous_url": previous_url, "next_url": next_url,
    }


def extract_article(html: str, source_url: str) -> dict:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return _extract_article_fallback(html, source_url)

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select(
        "script,style,noscript,iframe,svg,canvas,form,button,input,video,audio,"
        "nav,footer,header,aside,[role=navigation],[aria-hidden=true],"
        ".ad,.ads,.advert,.advertisement,.popup,.modal,.recommend,.related,.share,.social"
    ):
        tag.decompose()

    title_node = soup.select_one("h1")
    chapter_title = _clean_text(title_node.get_text(" ", strip=True) if title_node else "")
    document_title = _clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
    chapter_title = chapter_title or document_title or "未命名章节"
    work_title = ""
    for selector in ("meta[property='og:site_name']", "meta[name='application-name']"):
        node = soup.select_one(selector)
        if node and node.get("content"):
            work_title = _clean_text(node.get("content"))
            break
    if not work_title and document_title:
        # Common novel sites use "作品名 - 第十二章 标题". Remove the H1
        # rather than treating every chapter as a different work.
        remainder = _clean_text(document_title.replace(chapter_title, ""))
        remainder = re.sub(r"^[\s|_\-—·]+|[\s|_\-—·]+$", "", remainder)
        if remainder:
            work_title = remainder

    candidates = []
    selectors = (
        "article", "main", "[role=main]", ".chapter-content", ".chapter_content",
        ".read-content", ".reading-content", ".article-content", ".entry-content",
        "#content", "#chaptercontent", "#chapter-content", ".content",
    )
    seen_nodes = set()
    for node in soup.select(",".join(selectors)):
        if id(node) in seen_nodes:
            continue
        seen_nodes.add(id(node))
        text = _clean_text(node.get_text("\n", strip=True))
        if len(text) < MIN_ARTICLE_CHARS:
            continue
        link_chars = sum(len(a.get_text(" ", strip=True)) for a in node.find_all("a"))
        punctuation = len(re.findall(r"[。！？!?；;，,]", text))
        score = len(text) + punctuation * 12 - link_chars * 2
        candidates.append((score, node))
    if candidates:
        root = max(candidates, key=lambda item: item[0])[1]
    else:
        root = soup.body or soup

    paragraphs = []
    seen_text = set()
    blocks = root.find_all(["p", "blockquote", "li", "h2", "h3", "div"], recursive=True)
    for node in blocks:
        if node.name == "div" and node.find(["p", "blockquote", "li", "div"], recursive=False):
            continue
        text = _clean_text(node.get_text(" ", strip=True))
        if len(text) < 8 or text in seen_text:
            continue
        if len(node.find_all("a")) and sum(len(a.get_text(strip=True)) for a in node.find_all("a")) > len(text) * 0.45:
            continue
        if re.search(r"(最新网址|备用网址|备用网址|点击下载|加入书签|手机阅读|推荐阅读|猜你喜欢|广告合作)", text) and len(text) < 100:
            continue
        seen_text.add(text)
        paragraphs.append({"id": f"p{len(paragraphs) + 1}", "text": text})

    total = sum(len(p["text"]) for p in paragraphs)
    if total < MIN_ARTICLE_CHARS:
        raise ReadingError("没有识别出足够的正文。")

    previous_url = next_url = ""
    for link in soup.find_all("a", href=True):
        label = _clean_text(link.get_text(" ", strip=True)).lower()
        rel = " ".join(link.get("rel") or []).lower()
        if not previous_url and ("prev" in rel or re.search(r"上一[章节页]|上一章|前一章", label)):
            previous_url = _safe_chapter_link(source_url, link.get("href"))
        if not next_url and ("next" in rel or re.search(r"下一[章节页]|下一章|后一章", label)):
            next_url = _safe_chapter_link(source_url, link.get("href"))

    return {
        "title": chapter_title[:180],
        "work_title": (work_title or document_title or chapter_title)[:180],
        "paragraphs": paragraphs,
        "previous_url": previous_url,
        "next_url": next_url,
    }


async def _fetch_static(url: str) -> tuple[str, str]:
    import httpx
    current = await assert_public_url(url)
    timeout = httpx.Timeout(15.0, connect=5.0, read=10.0)
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8",
    }
    async with _HTTP_SEMAPHORE, httpx.AsyncClient(timeout=timeout, follow_redirects=False, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "")
                    if not location:
                        raise ReadingError("网站跳转异常。")
                    current = await assert_public_url(urljoin(current, location))
                    continue
                if response.status_code in {401, 403}:
                    raise ReadingError("这个页面需要登录、验证或拒绝抓取，系统不会绕过。")
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type and "text/plain" not in content_type:
                    raise ReadingError("这个链接不是可读取的网页正文。")
                announced = int(response.headers.get("content-length") or 0)
                if announced > MAX_RESPONSE_BYTES:
                    raise ReadingError("这个页面太大了。")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ReadingError("这个页面太大了。")
                encoding = response.encoding or "utf-8"
                return bytes(data).decode(encoding, errors="replace"), current
        raise ReadingError("网页跳转次数太多。")


async def _fetch_rendered(url: str) -> tuple[str, str]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise ReadingError("普通提取失败，浏览器提取组件尚未安装。") from exc

    target = await assert_public_url(url)
    origin_host = urlsplit(target).hostname or ""
    async with _BROWSER_SEMAPHORE:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox", "--disable-gpu"],
            )
            try:
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1",
                    viewport={"width": 390, "height": 844},
                    java_script_enabled=True,
                )
                page = await context.new_page()

                async def guard(route):
                    request = route.request
                    if request.resource_type in {"image", "media", "font", "stylesheet"}:
                        await route.abort()
                        return
                    try:
                        checked = await assert_public_url(request.url)
                        host = urlsplit(checked).hostname or ""
                        if host != origin_host and not host.endswith("." + origin_host):
                            await route.abort()
                            return
                    except ReadingError:
                        await route.abort()
                        return
                    await route.continue_()

                await page.route("**/*", guard)
                response = await page.goto(target, wait_until="domcontentloaded", timeout=18_000)
                if response and response.status in {401, 403}:
                    raise ReadingError("这个页面需要登录、验证或拒绝抓取，系统不会绕过。")
                await page.wait_for_timeout(1200)
                final_url = await assert_public_url(page.url)
                html = await page.content()
                if len(html.encode("utf-8")) > MAX_RESPONSE_BYTES:
                    raise ReadingError("渲染后的页面太大了。")
                return html, final_url
            finally:
                await browser.close()


async def fetch_article(url: str) -> dict:
    import httpx
    static_error = None
    try:
        html, final_url = await _fetch_static(url)
        article = extract_article(html, final_url)
        article["extraction"] = "http"
        article["source_url"] = final_url
        return article
    except (ReadingError, httpx.HTTPError) as exc:
        static_error = exc
    if isinstance(static_error, ReadingError) and "登录" in str(static_error):
        raise static_error
    try:
        html, final_url = await _fetch_rendered(url)
        article = extract_article(html, final_url)
        article["extraction"] = "browser"
        article["source_url"] = final_url
        return article
    except Exception as exc:
        if isinstance(exc, ReadingError):
            raise exc
        raise ReadingError("这次没有成功识别正文，原页面仍未被改动。") from exc


class ReadingStore:
    def __init__(self, base_dir: str):
        self.base = Path(base_dir) / "coreading"
        self.chapters = self.base / "chapters"
        self.works = self.base / "works"
        self._lock = threading.RLock()
        self.chapters.mkdir(parents=True, exist_ok=True)
        self.works.mkdir(parents=True, exist_ok=True)

    @property
    def state_path(self) -> Path:
        return self.base / "state.json"

    @property
    def index_path(self) -> Path:
        return self.base / "index.json"

    def _read(self, path: Path, default):
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return default

    def _write(self, path: Path, value) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".reading-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def find_by_url(self, url: str) -> dict | None:
        normalized = normalize_url(url)
        with self._lock:
            index = self._read(self.index_path, {})
            chapter_id = index.get(normalized)
            return self.get_chapter(chapter_id) if chapter_id else None

    def save_import(self, article: dict) -> dict:
        source_url = normalize_url(article["source_url"])
        content = "\n".join(p["text"] for p in article["paragraphs"])
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chapter_id = hashlib.sha256(source_url.encode("utf-8")).hexdigest()[:24]
        work_key = article.get("work_title") or urlsplit(source_url).hostname or "作品"
        work_id = hashlib.sha256(work_key.encode("utf-8")).hexdigest()[:20]
        with self._lock:
            index = self._read(self.index_path, {})
            content_key = f"sha256:{content_hash}"
            content_match = self.get_chapter(index.get(content_key))
            if not content_match:
                # Upgrade older URL-only indexes lazily without rewriting data.
                for candidate_id in set(index.values()):
                    candidate = self.get_chapter(candidate_id)
                    if candidate and candidate.get("content_hash") == content_hash:
                        content_match = candidate
                        break
            if content_match:
                index[source_url] = content_match["id"]
                index[content_key] = content_match["id"]
                self._write(self.index_path, index)
                self.set_state(
                    chapter_id=content_match["id"],
                    paragraph_id=(content_match.get("progress") or {}).get("paragraph_id") or "p1",
                )
                return content_match
            existing = self._read(self.chapters / f"{chapter_id}.json", {})
            chapter = {
                **existing,
                "schema": 1,
                "id": chapter_id,
                "work_id": work_id,
                "work_title": article.get("work_title") or article.get("title") or "未命名作品",
                "title": article.get("title") or "未命名章节",
                "source_url": source_url,
                "content_hash": content_hash,
                "paragraphs": article["paragraphs"],
                "previous_url": article.get("previous_url") or "",
                "next_url": article.get("next_url") or "",
                "extraction": article.get("extraction") or "http",
                "annotations": existing.get("annotations") or [],
                "progress": existing.get("progress") or {"paragraph_id": "p1", "percent": 0},
                "analysis": existing.get("analysis") if existing.get("content_hash") == content_hash else {
                    "status": "pending", "summary": "", "anchors": [], "content_hash": content_hash,
                },
                "created_at": existing.get("created_at") or utc_now(),
                "updated_at": utc_now(),
            }
            index[source_url] = chapter_id
            index[content_key] = chapter_id
            self._write(self.chapters / f"{chapter_id}.json", chapter)
            self._write(self.index_path, index)
            self.set_state(chapter_id=chapter_id, paragraph_id=chapter["progress"].get("paragraph_id") or "p1")
            return chapter

    def get_chapter(self, chapter_id: str | None) -> dict | None:
        if not chapter_id or not re.fullmatch(r"[0-9a-f]{24}", str(chapter_id)):
            return None
        with self._lock:
            value = self._read(self.chapters / f"{chapter_id}.json", None)
            return value if isinstance(value, dict) else None

    def set_state(self, *, chapter_id: str, paragraph_id: str = "", selection: str = "") -> dict:
        chapter = self.get_chapter(chapter_id)
        if not chapter:
            raise ReadingError("章节不存在。")
        valid_ids = {p["id"] for p in chapter.get("paragraphs") or []}
        paragraph_id = paragraph_id if paragraph_id in valid_ids else (chapter.get("progress") or {}).get("paragraph_id", "p1")
        selection = _clean_text(selection)[:1200]
        with self._lock:
            state = {
                "active": True,
                "chapter_id": chapter_id,
                "work_id": chapter["work_id"],
                "work_title": chapter["work_title"],
                "chapter_title": chapter["title"],
                "paragraph_id": paragraph_id,
                "selection": selection,
                "last_read_at": utc_now(),
            }
            self._write(self.state_path, state)
            chapter["progress"] = {"paragraph_id": paragraph_id, "updated_at": state["last_read_at"]}
            self._write(self.chapters / f"{chapter_id}.json", chapter)
            return state

    def get_state(self, include_context: bool = False) -> dict:
        with self._lock:
            state = self._read(self.state_path, {"active": False})
            if not state.get("active"):
                return {"active": False}
            chapter = self.get_chapter(state.get("chapter_id"))
            if not chapter:
                return {"active": False}
            result = dict(state)
            result["next_url"] = chapter.get("next_url") or ""
            result["previous_url"] = chapter.get("previous_url") or ""
            result["analysis_status"] = (chapter.get("analysis") or {}).get("status", "pending")
            if include_context:
                paragraphs = chapter.get("paragraphs") or []
                pos = next((i for i, p in enumerate(paragraphs) if p["id"] == state.get("paragraph_id")), 0)
                result["nearby"] = paragraphs[max(0, pos - 1):pos + 2]
                result["summary"] = str((chapter.get("analysis") or {}).get("summary") or "")[:2400]
                result["annotations"] = [
                    a for a in chapter.get("annotations") or []
                    if a.get("paragraph_id") in {p["id"] for p in result["nearby"]}
                ][-12:]
                result["work_memory"] = self.work_memory(chapter["work_id"])[-20:]
            return result

    def add_annotation(self, chapter_id: str, paragraph_id: str, text: str, *, author: str, kind: str) -> dict:
        if author not in {"闪闪", "Nikto"} or kind not in {"highlight", "comment"}:
            raise ReadingError("批注参数不正确。")
        text = _clean_text(text)[:3000]
        if not text:
            raise ReadingError("批注不能为空。")
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if not chapter or paragraph_id not in {p["id"] for p in chapter.get("paragraphs") or []}:
                raise ReadingError("找不到对应段落。")
            digest = hashlib.sha256(f"{chapter_id}|{paragraph_id}|{author}|{kind}|{text}".encode()).hexdigest()[:24]
            annotations = chapter.setdefault("annotations", [])
            existing = next((item for item in annotations if item.get("id") == digest), None)
            if existing:
                return existing
            item = {
                "id": digest, "paragraph_id": paragraph_id, "text": text,
                "author": author, "kind": kind, "created_at": utc_now(),
            }
            annotations.append(item)
            self._write(self.chapters / f"{chapter_id}.json", chapter)
            return item

    def save_analysis(self, chapter_id: str, analysis: dict) -> None:
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if not chapter:
                return
            anchors = []
            valid = {p["id"] for p in chapter.get("paragraphs") or []}
            for raw in analysis.get("anchors") or []:
                if not isinstance(raw, dict) or raw.get("paragraph_id") not in valid:
                    continue
                hint = _clean_text(raw.get("hint"))[:500]
                if hint:
                    anchors.append({"paragraph_id": raw["paragraph_id"], "hint": hint, "shown": False})
            chapter["analysis"] = {
                "status": "ready",
                "summary": _clean_text(analysis.get("summary"))[:5000],
                "anchors": anchors[:6],
                "content_hash": chapter.get("content_hash"),
                "updated_at": utc_now(),
            }
            self._write(self.chapters / f"{chapter_id}.json", chapter)
            self.upsert_work_memory(chapter["work_id"], analysis.get("memory") or [])

    def set_anno_book_id(self, chapter_id: str, book_id: str) -> None:
        if not book_id:
            return
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if chapter:
                chapter["anno_book_id"] = str(book_id)[:120]
                chapter["anno_synced_at"] = utc_now()
                self._write(self.chapters / f"{chapter_id}.json", chapter)

    def mark_annotation_synced(self, chapter_id: str, annotation_id: str) -> None:
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if not chapter:
                return
            for item in chapter.get("annotations") or []:
                if item.get("id") == annotation_id:
                    item["anno_synced_at"] = utc_now()
                    self._write(self.chapters / f"{chapter_id}.json", chapter)
                    return

    def mark_analysis_failed(self, chapter_id: str) -> None:
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if chapter:
                chapter["analysis"] = {**(chapter.get("analysis") or {}), "status": "failed", "updated_at": utc_now()}
                self._write(self.chapters / f"{chapter_id}.json", chapter)

    def claim_anchor(self, chapter_id: str, paragraph_id: str) -> dict | None:
        with self._lock:
            chapter = self.get_chapter(chapter_id)
            if not chapter:
                return None
            for anchor in (chapter.get("analysis") or {}).get("anchors") or []:
                if anchor.get("paragraph_id") == paragraph_id and not anchor.get("shown"):
                    anchor["shown"] = True
                    anchor["shown_at"] = utc_now()
                    self._write(self.chapters / f"{chapter_id}.json", chapter)
                    return dict(anchor)
            return None

    def upsert_work_memory(self, work_id: str, entries: list) -> None:
        if not re.fullmatch(r"[0-9a-f]{20}", str(work_id)):
            return
        path = self.works / f"{work_id}.json"
        with self._lock:
            data = self._read(path, {"schema": 1, "entries": []})
            current = data.get("entries") or []
            for raw in entries if isinstance(entries, list) else []:
                if not isinstance(raw, dict):
                    continue
                kind = str(raw.get("kind") or "plot")[:24]
                key = _clean_text(raw.get("key"))[:160]
                value = _clean_text(raw.get("value"))[:1200]
                if not key or not value:
                    continue
                normalized = re.sub(r"\W+", "", key).lower()
                found = None
                for item in current:
                    if item.get("kind") != kind:
                        continue
                    old_key = str(item.get("normalized_key") or "")
                    if old_key == normalized:
                        found = item
                        break
                    ratio = difflib.SequenceMatcher(None, old_key, normalized).ratio()
                    left, right = set(old_key), set(normalized)
                    overlap = len(left & right) / max(1, len(left | right))
                    if ratio >= 0.82 or overlap >= 0.86:
                        found = item
                        break
                if found:
                    found.update({"key": key, "value": value, "updated_at": utc_now()})
                else:
                    current.append({
                        "id": hashlib.sha256(f"{kind}|{normalized}".encode()).hexdigest()[:20],
                        "kind": kind, "key": key, "normalized_key": normalized,
                        "value": value, "updated_at": utc_now(),
                    })
            data["entries"] = current[-300:]
            self._write(path, data)

    def work_memory(self, work_id: str) -> list[dict]:
        if not re.fullmatch(r"[0-9a-f]{20}", str(work_id)):
            return []
        return list(self._read(self.works / f"{work_id}.json", {"entries": []}).get("entries") or [])
