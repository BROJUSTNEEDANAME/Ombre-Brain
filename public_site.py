"""Resolve Ombre's real public URL without committing its private path."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit, urlunsplit


_SITE_HEADER = re.compile(r"(?m)^\s*(https?://[^\s{]+)\s*\{")
_HANDLE_PATH = re.compile(r"handle_path\s+(/[^\s{*]+)/\*\s*\{")
_BRAIN_PROXY = re.compile(
    r"\breverse_proxy\s+(?:https?://)?(?:127\.0\.0\.1|localhost):8000\b"
)


def _braced_block(text: str, opening_brace: int) -> str:
    depth = 0
    for index in range(opening_brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[opening_brace + 1 : index]
    return ""


def _public_listener_url(site_url: str) -> str:
    """Tailscale Funnel maps the local Caddy 8443 listener to public HTTPS 443."""
    parsed = urlsplit(site_url)
    if parsed.hostname and parsed.hostname.endswith(".ts.net") and parsed.port == 8443:
        return urlunsplit(
            (parsed.scheme, parsed.hostname, parsed.path, parsed.query, parsed.fragment)
        )
    return site_url


def infer_caddy_site_url(caddy_text: str) -> str:
    """Find the HTTPS site and handle_path that proxy to Ombre on port 8000."""
    text = re.sub(r"(?m)#.*$", "", caddy_text or "")
    for site_match in _SITE_HEADER.finditer(text):
        site_url = _public_listener_url(site_match.group(1).rstrip("/"))
        opening = text.find("{", site_match.start())
        site_block = _braced_block(text, opening)
        if not site_block:
            continue
        for path_match in _HANDLE_PATH.finditer(site_block):
            route_opening = site_block.find("{", path_match.start())
            route_block = _braced_block(site_block, route_opening)
            if route_block and _BRAIN_PROXY.search(route_block):
                return site_url + path_match.group(1).rstrip("/")
        if _BRAIN_PROXY.search(site_block):
            return site_url
    return ""


def resolve_public_site_url(
    environ: Mapping[str, str] | None = None,
    caddy_path: str | os.PathLike[str] | None = None,
) -> str:
    """Use the live private Caddy route before any possibly stale environment."""
    env = os.environ if environ is None else environ
    path = Path(caddy_path or env.get("OMBRE_CADDYFILE", "/etc/caddy/Caddyfile"))
    try:
        inferred = infer_caddy_site_url(path.read_text(encoding="utf-8"))
    except OSError:
        inferred = ""
    if inferred:
        return inferred

    explicit = str(env.get("OMBRE_SITE_URL", "")).strip()
    if explicit:
        return explicit.rstrip("/")

    render_url = str(env.get("RENDER_EXTERNAL_URL", "")).strip()
    return render_url.rstrip("/")
