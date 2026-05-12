#!/usr/bin/env python3
"""
unpin_all.py — 批量取消现有 brain 桶的钉选状态。

用途:dashboard 上手动 hold 进去的桶默认 pinned=True、importance=10、
weight=999,会让它们永不衰减,失去 ombre brain 的语义。这个脚本走 MCP
streamable-http 协议调用 trace 工具批量解除钉选(pinned=0),
让权重恢复正常衰减。

用法:
    python scripts/unpin_all.py                # 默认线上 brain
    python scripts/unpin_all.py http://localhost:8000  # 本地

不会修改内容、不会删除任何桶。
"""

import json
import sys
import uuid
import urllib.request
import urllib.error


def post_jsonrpc(base_url, payload, session_id=None):
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["mcp-session-id"] = session_id

    req = urllib.request.Request(
        f"{base_url}/mcp",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        sid = resp.headers.get("mcp-session-id")
        raw = resp.read().decode("utf-8", errors="replace")
        return sid, raw


def parse_sse(raw):
    """Extract JSON from an SSE 'data: {...}' stream."""
    for line in raw.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if chunk and chunk != "[DONE]":
                try:
                    return json.loads(chunk)
                except json.JSONDecodeError:
                    pass
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def initialize(base_url):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "unpin-all", "version": "1.0"},
        },
    }
    sid, raw = post_jsonrpc(base_url, payload)
    if not sid:
        raise RuntimeError("server did not return mcp-session-id")

    # Send initialized notification
    notify = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    post_jsonrpc(base_url, notify, sid)
    return sid


def list_buckets(base_url):
    req = urllib.request.Request(f"{base_url}/api/buckets")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def trace_unpin(base_url, sid, bucket_id):
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "tools/call",
        "params": {
            "name": "trace",
            "arguments": {"bucket_id": bucket_id, "pinned": 0, "importance": 5},
        },
    }
    _, raw = post_jsonrpc(base_url, payload, sid)
    return parse_sse(raw)


def main():
    base_url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "https://ombre-brain-6e05.onrender.com"

    print(f"→ target: {base_url}")
    buckets = list_buckets(base_url)
    pinned = [b for b in buckets if b.get("pinned")]
    print(f"→ {len(buckets)} buckets total, {len(pinned)} pinned")

    if not pinned:
        print("nothing to do.")
        return

    print("\npinned buckets:")
    for b in pinned:
        preview = (b.get("content_preview") or "")[:60].replace("\n", " ")
        print(f"  {b['id']}  {preview}")

    confirm = input("\nunpin all of these? [y/N] ").strip().lower()
    if confirm != "y":
        print("aborted.")
        return

    sid = initialize(base_url)
    print(f"→ mcp session: {sid}\n")

    ok, fail = 0, 0
    for b in pinned:
        result = trace_unpin(base_url, sid, b["id"])
        if result and "result" in result:
            ok += 1
            print(f"  ✓ {b['id']}")
        else:
            fail += 1
            print(f"  ✗ {b['id']}  ({result})")

    print(f"\ndone. {ok} unpinned, {fail} failed.")


if __name__ == "__main__":
    main()
