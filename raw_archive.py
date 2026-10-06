"""聊天原文逐字检索（学 paramecium：原文是唯一真相，搜到什么就是什么）。

由来 2026-10-06：她问「Eden」，他搜记忆库没找着。桶是摘要，摘要会把名字吃掉——
归档里那两条前夫哥的桶，一条都没写 Eden。原话在聊天存档里（cc_bridge 每天往
~/ombre-archive/telegram/YYYY-MM-DD.md 追加 `[HH:MM] 谁: 原话`），却只有他自己想起来
去 grep 才看得到。这里让 breath 带关键词时**顺手把原话也翻出来**，一字不改。

零依赖，纯函数，测试能真的跑。
"""
from __future__ import annotations

import os
import re

DEFAULT_DIR = os.path.expanduser(os.environ.get("OMBRE_ARCHIVE_DIR", "~/ombre-archive"))
_EXTS = (".md", ".txt")


def _terms(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    parts = [p for p in re.split(r"[\s,，、]+", q) if p]
    # 整句优先；整句以外的单词至少 2 个字，免得「的」「a」把全库刷出来
    out = [q] + [p for p in parts if len(p) >= 2 and p != q]
    seen, uniq = set(), []
    for t in out:
        k = t.casefold()
        if k not in seen:
            seen.add(k); uniq.append(t)
    return uniq


def _files(base: str) -> list[str]:
    found = []
    for root, _dirs, names in os.walk(base):
        for n in names:
            if n.endswith(_EXTS):
                found.append(os.path.join(root, n))
    # 文件名一般是日期：新的在前
    return sorted(found, key=lambda p: os.path.basename(p), reverse=True)


def search(query: str, base_dir: str | None = None, *, max_hits: int = 6,
           context: int = 2, max_chars: int = 1500) -> str:
    """返回格式化好的原文片段；没命中/目录不存在就返回空串（没结果好过垃圾结果）。"""
    base = base_dir or DEFAULT_DIR
    terms = _terms(query)
    if not terms or not os.path.isdir(base):
        return ""
    # 整句在哪儿都没出现过，才退到逐词
    for use in ([terms[0]], terms[1:]):
        if not use:
            continue
        keys = [t.casefold() for t in use]
        blocks: list[str] = []
        for path in _files(base):
            try:
                lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
            except OSError:
                continue
            last_end = -1
            for i, line in enumerate(lines):
                if not any(k in line.casefold() for k in keys):
                    continue
                if i <= last_end:          # 已经包在上一段上下文里了
                    continue
                a, b = max(0, i - context), min(len(lines), i + context + 1)
                rel = os.path.relpath(path, base)
                blocks.append(f"〔{rel} 第{i + 1}行〕\n" + "\n".join(lines[a:b]))
                last_end = b - 1
                if len(blocks) >= max_hits:
                    break
            if len(blocks) >= max_hits:
                break
        if blocks:
            out, used = [], 0
            for blk in blocks:
                if used + len(blk) > max_chars:
                    if not out:            # 至少给一段，截断也比空手强
                        out.append(blk[:max_chars] + "…")
                    break
                out.append(blk); used += len(blk)
            return "\n\n".join(out)
    return ""
