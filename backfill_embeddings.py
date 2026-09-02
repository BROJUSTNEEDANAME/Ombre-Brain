#!/usr/bin/env python3
"""
Backfill embeddings for existing buckets.
为存量桶批量生成 embedding。

Usage:
    OMBRE_BUCKETS_DIR=/data OMBRE_API_KEY=xxx python backfill_embeddings.py [--batch-size 20] [--dry-run]

Each batch calls Gemini embedding API once per bucket.
Free tier: 1500 requests/day, so ~75 batches of 20.
"""

import asyncio
import argparse
import os
import sys
import time

sys.path.insert(0, ".")
import env_file
# key 在 systemd 的 EnvironmentFile 里，shell 里没有。不先加载就是
# 「ERROR: missing API key」——她已经撞过一次。
env_file.load()
from utils import load_config
from bucket_manager import BucketManager
from embedding_engine import EmbeddingEngine


async def backfill(batch_size: int = 20, dry_run: bool = False):
    config = load_config()
    bucket_mgr = BucketManager(config)
    engine = EmbeddingEngine(config)

    if not engine.enabled:
        # ⚠️ 必须让调用方知道失败了。原来只 print 就 return，退出码仍是 0，
        # 外面的脚本据此报「补完了」——她看到的是一句假话（踩过）。
        have = [n for n in ("OMBRE_API_KEY", "OMBRE_EMBED_API_KEY")
                if os.environ.get(n, "").strip()]
        print("ERROR: 补向量没跑起来——没读到 embedding 用的 API key。")
        print("  需要 OMBRE_API_KEY 或 OMBRE_EMBED_API_KEY 其中之一"
              "（也可以写在 config.yaml 的 embedding.api_key）。")
        print(f"  当前环境里设了的：{'、'.join(have) if have else '一个都没有'}")
        return 1

    all_buckets = await bucket_mgr.list_all(include_archive=True)
    print(f"Total buckets: {len(all_buckets)}")

    # Find buckets without embeddings
    missing = []
    for b in all_buckets:
        emb = await engine.get_embedding(b["id"])
        if emb is None:
            missing.append(b)

    print(f"Missing embeddings: {len(missing)}")

    if dry_run:
        for b in missing[:10]:
            print(f"  would embed: {b['id']} ({b['metadata'].get('name', '?')})")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")
        return

    total = len(missing)
    success = 0
    failed = 0

    for i in range(0, total, batch_size):
        batch = missing[i : i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        print(f"\n--- Batch {batch_num}/{total_batches} ({len(batch)} buckets) ---")

        for b in batch:
            name = b["metadata"].get("name", b["id"])
            content = b.get("content", "")
            if not content or not content.strip():
                print(f"  SKIP (empty): {b['id']} ({name})")
                continue

            try:
                ok = await engine.generate_and_store(b["id"], content)
                if ok:
                    success += 1
                    print(f"  OK: {b['id'][:12]} ({name[:30]})")
                else:
                    failed += 1
                    print(f"  FAIL: {b['id'][:12]} ({name[:30]})")
            except Exception as e:
                failed += 1
                print(f"  ERROR: {b['id'][:12]} ({name[:30]}): {e}")

        if i + batch_size < total:
            print(f"  Waiting 2s before next batch...")
            await asyncio.sleep(2)

    print(f"\n=== Done: {success} success, {failed} failed, {total - success - failed} skipped ===")
    # 一条都没成功、却有该补的 → 别报「补完了」，退出码要红
    return 1 if (failed and not success) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(backfill(batch_size=args.batch_size, dry_run=args.dry_run)) or 0)
