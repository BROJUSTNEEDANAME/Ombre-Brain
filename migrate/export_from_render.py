#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_from_render.py —— 把 Render 上正在跑的 Ombre Brain 记忆，全量导出到本地 buckets 目录。

原理 / How it works:
  调用大脑的只读接口 /api/buckets（列表）+ /api/bucket/{id}（正文+元数据），
  用与服务端完全相同的 python-frontmatter 格式重建每个桶的 .md 文件，落到目标目录，
  并按 permanent / dynamic / feel + 主题域 还原目录结构。
  向量(embeddings.db)不走这里 —— 导完在 VPS 上跑 backfill_embeddings.py 重新生成即可。

用法 / Usage（在 VPS 上跑，VPS 能连 Render）:
    cd ~/Ombre-Brain
    python migrate/export_from_render.py \
        --url https://ombre-brain-6e05.onrender.com \
        --out ./buckets

注意:
  - Render 冷启动时第一条请求可能等几十秒，脚本会自动重试，属正常现象，耐心等。
  - 只依赖标准库 + python-frontmatter（仓库本来就装了）。
"""
import argparse
import json
import os
import sys
import time
import urllib.request

# 让脚本能 import 到仓库根目录的 utils（保证文件名清洗与服务端一致）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import frontmatter  # noqa: E402
from utils import sanitize_name  # noqa: E402


def fetch(url, retries=6, timeout=120):
    """GET url -> JSON，带指数退避重试（应对 Render 冷启动）。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "ombre-migrate/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            last = e
            wait = 2 ** i
            print(f"  · 请求失败 ({i+1}/{retries}) {url} -> {e}；等 {wait}s 重试"
                  f"（Render 冷启动很正常）", flush=True)
            time.sleep(wait)
    raise last


def bucket_subdir(meta):
    """按服务端逻辑决定桶存到哪个子目录。"""
    btype = meta.get("type", "dynamic")
    pinned = bool(meta.get("pinned"))
    if btype == "feel":
        return os.path.join("feel", "沉淀物")
    dom = meta.get("domain") or ["未分类"]
    primary = sanitize_name(dom[0]) if dom else "未分类"
    if btype == "permanent" or pinned:
        return os.path.join("permanent", primary)
    return os.path.join("dynamic", primary)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True,
                    help="Render 大脑根地址，如 https://ombre-brain-6e05.onrender.com")
    ap.add_argument("--out", required=True,
                    help="导出到的 buckets 目录（会自动建子目录）")
    args = ap.parse_args()
    base = args.url.rstrip("/")
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    print(f"[1/3] 拉取桶列表：{base}/api/buckets", flush=True)
    buckets = fetch(f"{base}/api/buckets")
    print(f"      共 {len(buckets)} 个桶", flush=True)

    ok, fail = 0, []
    for i, b in enumerate(buckets, 1):
        bid = b["id"]
        try:
            detail = fetch(f"{base}/api/bucket/{bid}")
            meta = dict(detail.get("metadata", {}))
            content = detail.get("content", "") or ""
            target_dir = os.path.join(out, bucket_subdir(meta))
            os.makedirs(target_dir, exist_ok=True)
            name = meta.get("name") or bid
            fname = (f"{sanitize_name(name)}_{bid}.md"
                     if name and name != bid else f"{bid}.md")
            post = frontmatter.Post(content, **meta)
            with open(os.path.join(target_dir, fname), "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))
            ok += 1
            if i % 20 == 0:
                print(f"      {i}/{len(buckets)} ...", flush=True)
        except Exception as e:  # noqa: BLE001
            fail.append((bid, str(e)))
            print(f"  ! 桶 {bid} 导出失败: {e}", flush=True)

    print(f"[2/3] 导出完成：成功 {ok}，失败 {len(fail)}", flush=True)
    if fail:
        print("      失败列表：", fail, flush=True)
    print(f"[3/3] 目标目录：{out}", flush=True)
    print("      下一步：把它设为 OMBRE_BUCKETS_DIR，并在 VPS 上跑"
          " `python backfill_embeddings.py` 重建向量。", flush=True)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
