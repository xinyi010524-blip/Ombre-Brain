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
import sys

sys.path.insert(0, ".")
from utils import load_config
from bucket_manager import BucketManager
from embedding_engine import EmbeddingEngine


async def backfill(batch_size: int = 20, dry_run: bool = False):
    config = load_config()
    bucket_mgr = BucketManager(config)
    engine = EmbeddingEngine(config)

    if not engine.enabled:
        print("ERROR: Embedding engine not enabled (missing API key?)")
        return

    # --- Preflight: show resolved embedding config (key masked) ---
    # --- 预检：打印解析后的 embedding 配置（密钥脱敏）---
    key = engine.api_key or ""
    masked = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else ("***" if key else "(none)")
    print("=== Embedding preflight / 预检 ===")
    print(f"  model    : {engine.model}")
    print(f"  base_url : {engine.base_url}")
    print(f"  api_key  : {masked}")
    print(f"  db_path  : {engine.db_path}")

    # --- Preflight: one live test call so a misconfig fails fast with a clear msg ---
    # --- 预检：先做一次真实 embedding 调用，配置错误时立即失败、给出明确提示 ---
    if not dry_run:
        print("  test call ... ", end="", flush=True)
        try:
            vec = await engine._generate_embedding("preflight connectivity test")
        except Exception as e:
            vec = None
            print("FAILED")
            print(f"ERROR: embedding test call raised: {e}")
        else:
            if vec:
                print(f"OK (dim={len(vec)})")
            else:
                print("FAILED")
        if not vec:
            print(
                "Aborting before backfill. Check that base_url matches the api_key's "
                "provider and that the model name is valid for that endpoint.\n"
                "常见原因：base_url 指向脱水用的供应商(如 DeepSeek)，但 model 是 "
                "gemini-embedding-001 —— 二者不匹配。请设置 OMBRE_EMBEDDING_BASE_URL / "
                "OMBRE_EMBEDDING_API_KEY 指向正确的 embedding 供应商。"
            )
            return

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
            print("  Waiting 2s before next batch...")
            await asyncio.sleep(2)

    print(f"\n=== Done: {success} success, {failed} failed, {total - success - failed} skipped ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(backfill(batch_size=args.batch_size, dry_run=args.dry_run))
