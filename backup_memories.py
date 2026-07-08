# -*- coding: utf-8 -*-
"""
把 Render 大脑上的全部记忆拉到本地做备份。
Backup every memory bucket from the (remote) brain to local disk.

用法 / Usage:
    python3 backup_memories.py
    # 或指定地址：
    OMBRE_BRAIN_URL=https://ombre-brain-6e05.onrender.com python3 backup_memories.py

产出 / Output（存到 ./ombre_backup_<日期戳>/）：
    all_buckets.json          —— 所有桶的完整数据（元信息 + 正文），一个大 JSON
    buckets_json/<id>.json    —— 每条记忆单独一份 JSON（最保真，绝不丢字段）
    buckets_md/<id>.md        —— 每条记忆重建成 Markdown+frontmatter（可直接塞回大脑）

只读远程接口，不改任何东西。纯 stdlib，不依赖第三方库。
"""

import json
import os
import sys
import urllib.request
import urllib.error

BRAIN_URL = os.environ.get("OMBRE_BRAIN_URL", "https://ombre-brain-6e05.onrender.com").rstrip("/")
TIMEOUT = 60


def _get(path):
    url = BRAIN_URL + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _yaml_frontmatter(meta):
    """极简 YAML frontmatter 序列化（够用，重建 .md 用）。"""
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, tuple)):
            inner = ", ".join(json.dumps(x, ensure_ascii=False) for x in v)
            lines.append(f"{k}: [{inner}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        elif v is None:
            lines.append(f"{k}: null")
        elif isinstance(v, (int, float)):
            lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {json.dumps(str(v), ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines)


def main():
    print(f"[备份] 大脑地址: {BRAIN_URL}", flush=True)
    print("[备份] 正在拉取记忆列表 …（Render 冷启动可能要等几十秒）", flush=True)

    try:
        buckets = _get("/api/buckets")
    except urllib.error.URLError as e:
        print(f"[备份] 连不上大脑: {e}", flush=True)
        print("[备份] 确认 Render 服务已开启、地址正确后重试。", flush=True)
        sys.exit(1)

    total = len(buckets)
    print(f"[备份] 共发现 {total} 条记忆，开始逐条拉取完整内容 …", flush=True)

    # 输出目录（日期戳由系统 date 命令预先算好，避免脚本里取时间）
    stamp = os.environ.get("BACKUP_STAMP", "latest")
    outdir = os.path.join(os.getcwd(), f"ombre_backup_{stamp}")
    json_dir = os.path.join(outdir, "buckets_json")
    md_dir = os.path.join(outdir, "buckets_md")
    os.makedirs(json_dir, exist_ok=True)
    os.makedirs(md_dir, exist_ok=True)

    full = []
    ok = 0
    fail = 0
    for i, b in enumerate(buckets, 1):
        bid = b.get("id")
        if not bid:
            continue
        try:
            detail = _get(f"/api/bucket/{bid}")
        except Exception as e:  # noqa: BLE001
            print(f"  [{i}/{total}] {bid} 拉取失败: {e}", flush=True)
            fail += 1
            continue

        full.append(detail)

        # 每条单独存 JSON（最保真）
        with open(os.path.join(json_dir, f"{bid}.json"), "w", encoding="utf-8") as f:
            json.dump(detail, f, ensure_ascii=False, indent=2)

        # 重建 .md（可直接塞回大脑的 buckets 目录）
        meta = detail.get("metadata", {})
        content = detail.get("content", "")
        with open(os.path.join(md_dir, f"{bid}.md"), "w", encoding="utf-8") as f:
            f.write(_yaml_frontmatter(meta) + "\n\n" + content + "\n")

        ok += 1
        if i % 10 == 0 or i == total:
            print(f"  已备份 {i}/{total} …", flush=True)

    # 一个大 JSON 兜底
    with open(os.path.join(outdir, "all_buckets.json"), "w", encoding="utf-8") as f:
        json.dump(full, f, ensure_ascii=False, indent=2)

    print("", flush=True)
    print(f"[备份] 完成！成功 {ok} 条，失败 {fail} 条。", flush=True)
    print(f"[备份] 全部存到：{outdir}", flush=True)
    print(f"[备份]   - all_buckets.json （全量）", flush=True)
    print(f"[备份]   - buckets_json/    （逐条 JSON，最保真）", flush=True)
    print(f"[备份]   - buckets_md/      （重建的 Markdown）", flush=True)


if __name__ == "__main__":
    main()
