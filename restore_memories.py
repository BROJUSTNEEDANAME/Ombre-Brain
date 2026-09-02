# -*- coding: utf-8 -*-
"""
把 backup_memories.py 备份下来的记忆，塞回本机大脑的 buckets 目录。
Restore buckets produced by backup_memories.py into a local brain.

用法 / Usage:
    python3 restore_memories.py ombre_backup_20260902            # 先看会做什么
    python3 restore_memories.py ombre_backup_20260902 --write    # 真的写入

默认 dry-run：只打印「会新增哪些、会跳过哪些」，一个字都不写。
加 --write 才真的落盘。

规则：
  * 原来的 bucket_id 一律保留 —— feel 的 source_bucket、各种引用才不会断。
  * 本机已经有同一个 bucket_id 的，**跳过**，绝不覆盖本机版本。
      （本机那份可能已经被 dream/trace 改过，比备份新。）
  * 按 type（permanent / dynamic / feel）+ 第一个 domain 放进对应子目录，
    和 bucket_manager 写文件的规则一致。

纯 stdlib，不依赖第三方库；不联网。
"""

import json
import os
import re
import sys

BUCKETS_DIR = os.environ.get("OMBRE_BUCKETS_DIR", "./buckets")
_UNSAFE = re.compile(r"[^0-9A-Za-z一-鿿._-]+")


def sanitize(name: str, fallback: str = "") -> str:
    """和 bucket_manager.sanitize_name 一个目的：别让名字变成路径。
    域名空了要落到「未分类」，桶名空了就是空（文件名只用 id）——
    两者不能共用一个兜底值。"""
    cleaned = _UNSAFE.sub("_", str(name or "")).strip("._-")
    return cleaned[:60] or fallback


def existing_ids(base_dir: str) -> set[str]:
    """本机已有的 bucket_id。文件名是 <名字>_<id>.md 或 <id>.md，
    但别从文件名猜——直接读 frontmatter 里的 id，最准。"""
    found: set[str] = set()
    for root, _, files in os.walk(base_dir):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    head = f.read(2000)
            except OSError:
                continue
            m = re.search(r'^id:\s*"?([A-Za-z0-9_-]+)"?\s*$', head, re.M)
            if m:
                found.add(m.group(1))
            elif fn.endswith(".md"):
                found.add(fn[:-3].rsplit("_", 1)[-1])
    return found


def target_path(base_dir: str, meta: dict) -> str:
    btype = str(meta.get("type") or "dynamic")
    if btype == "feel":
        sub, domain = "feel", "沉淀物"
    else:
        sub = "permanent" if btype == "permanent" else "dynamic"
        domains = meta.get("domain") or []
        if isinstance(domains, str):
            domains = [domains]
        domain = sanitize(domains[0], "未分类") if domains else "未分类"
    bid = str(meta.get("id") or "")
    name = sanitize(meta.get("name") or "")
    fn = f"{name}_{bid}.md" if name and name != bid else f"{bid}.md"
    return os.path.join(base_dir, sub, domain, fn)


def frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, (list, tuple)):
            lines.append(f"{k}: [" + ", ".join(json.dumps(x, ensure_ascii=False) for x in v) + "]")
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


def load_backup(backup_dir: str) -> list[dict]:
    path = os.path.join(backup_dir, "all_buckets.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return [b for b in data if isinstance(b, dict)]


def restore(backup_dir: str, base_dir: str = BUCKETS_DIR, write: bool = False) -> dict:
    have = existing_ids(base_dir)
    added, skipped, bad = [], [], []
    for bucket in load_backup(backup_dir):
        meta = dict(bucket.get("metadata") or {})
        bid = str(meta.get("id") or bucket.get("id") or "")
        if not bid:
            bad.append(bucket)
            continue
        meta.setdefault("id", bid)
        if bid in have:
            skipped.append(bid)
            continue
        path = target_path(base_dir, meta)
        if write:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(frontmatter(meta) + "\n\n" + str(bucket.get("content") or "") + "\n")
        have.add(bid)
        added.append(bid)
    return {"added": added, "skipped": skipped, "bad": len(bad), "wrote": write}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    write = "--write" in sys.argv
    if not args:
        print(__doc__)
        sys.exit(1)
    result = restore(args[0], BUCKETS_DIR, write=write)
    print(f"[恢复] 大脑目录: {BUCKETS_DIR}")
    print(f"[恢复] 会新增 {len(result['added'])} 条，跳过（本机已有）{len(result['skipped'])} 条"
          + (f"，坏数据 {result['bad']} 条" if result["bad"] else ""))
    if not write:
        print("[恢复] 这是预演，什么都没写。确认没问题就加 --write 再跑一次。")
    else:
        print("[恢复] 已写入。记得重启大脑：systemctl restart ombre-brain")
        print("[恢复] 新记忆还没有向量，跑一下 backfill_embeddings.py 才能被语义检索到。")


if __name__ == "__main__":
    main()
