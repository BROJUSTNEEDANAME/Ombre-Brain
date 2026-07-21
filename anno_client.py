"""Narrow, non-destructive REST bridge to the independent Anno service."""

from __future__ import annotations

import os
import re

import httpx


class AnnoClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("ANNO_BASE_URL") or "http://127.0.0.1:3300").rstrip("/")
        self.token = (os.environ.get("ANNO_REST_TOKEN") or "").strip()

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    async def healthy(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(self.base_url + "/health", headers=self._headers())
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def import_text(self, title: str, paragraphs: list[dict]) -> str:
        text = "\n\n".join(str(item.get("text") or "") for item in paragraphs).strip()
        if not text:
            return ""
        filename = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", title)[:80] or "chapter"
        async with httpx.AsyncClient(timeout=125.0) as client:
            response = await client.post(
                self.base_url + "/api/upload-book",
                headers=self._headers(),
                files={"file": (filename + ".txt", text.encode("utf-8"), "text/plain")},
                data={"title": title[:180]},
            )
        response.raise_for_status()
        data = response.json()
        return str(data.get("id") or data.get("book_id") or "")

    async def annotate(self, book_id: str, paragraph_id: str, text: str, *, author: str, kind: str) -> None:
        if not book_id:
            return
        number = int(str(paragraph_id).lstrip("p") or 0)
        payload = {
            "paragraph_id": number,
            "type": "highlight" if kind == "highlight" else "note",
            "text": text[:3000],
            "author": author,
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(
                f"{self.base_url}/api/books/{book_id}/annotations",
                headers=self._headers(), json=payload,
            )
        response.raise_for_status()
