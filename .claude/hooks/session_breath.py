#!/usr/bin/env python3
# ============================================================
# SessionStart Hook: auto-breath + dreaming on session start
# 对话开始钩子：自动浮现记忆 + 触发 dreaming
#
# On SessionStart this surfaces memories into Claude's context so
# every conversation starts with a "breath". It works in two modes:
#
#   1. In-process (default): import the Ombre Brain server and run the
#      breath/dream logic directly — no running HTTP server needed.
#      This is what makes auto-breath work with the local stdio MCP
#      setup (.mcp.json).
#   2. HTTP: if OMBRE_HOOK_URL is set, call the running server's
#      /breath-hook and /dream-hook endpoints instead (use this when
#      you run Ombre Brain in streamable-http / Docker mode).
#
# Sequence: breath → dream
# 顺序：呼吸浮现 → 做梦消化
#
# Config:
#   OMBRE_HOOK_URL  — if set, use this server URL over HTTP instead of
#                     running in-process (e.g. http://localhost:8000)
#   OMBRE_HOOK_SKIP — set to "1" to disable the hook temporarily
#
# The hook never raises: if memories can't be surfaced (deps missing,
# server down, no memories yet) it exits quietly without blocking the
# conversation.
# ============================================================

import os
import sys


def main():
    # Allow disabling the hook via env var / 允许通过环境变量关闭
    if os.environ.get("OMBRE_HOOK_SKIP") == "1":
        sys.exit(0)

    hook_url = os.environ.get("OMBRE_HOOK_URL", "").strip()
    if hook_url:
        _run_http(hook_url.rstrip("/"))
    else:
        _run_inprocess()


# ------------------------------------------------------------
# In-process mode: import the server module and run breath/dream
# directly. Reuses the exact same logic as the HTTP endpoints.
# ------------------------------------------------------------
def _run_inprocess():
    try:
        import asyncio

        # Make sure the project root is importable when the hook is
        # launched from elsewhere.
        project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        if project_dir not in sys.path:
            sys.path.insert(0, project_dir)

        import server  # noqa: E402

        async def _gather():
            out = []
            for handler in (server.breath_hook, server.dream_hook):
                try:
                    resp = await handler(None)
                    text = resp.body.decode("utf-8").strip()
                    if text:
                        out.append(text)
                except Exception:
                    pass
            return out

        parts = asyncio.run(_gather())
        if parts:
            print("\n\n".join(parts))
    except Exception:
        # Deps not installed, no config, etc. — fail silently.
        pass


# ------------------------------------------------------------
# HTTP mode: call a running Ombre Brain server's hook endpoints.
# ------------------------------------------------------------
def _run_http(base_url):
    import urllib.request
    import urllib.error

    # Per-request timeout (seconds). Render free tier cold-starts can take
    # much longer than this — if so the hook gives up quietly and Claude
    # surfaces memories itself via the mandatory breath() MCP call.
    try:
        timeout = float(os.environ.get("OMBRE_HOOK_TIMEOUT", "10"))
    except ValueError:
        timeout = 10.0

    for path in ("/breath-hook", "/dream-hook"):
        try:
            req = urllib.request.Request(
                f"{base_url}{path}",
                headers={"Accept": "text/plain"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                output = response.read().decode("utf-8").strip()
                if output:
                    print(output)
        except (urllib.error.URLError, OSError):
            pass
        except Exception:
            pass


if __name__ == "__main__":
    main()
