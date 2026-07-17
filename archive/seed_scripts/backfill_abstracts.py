"""
Batch backfill abstracts for draft patent listings that don't have one yet.

Two sources via collabv.abstract_fetcher:
  - Primary: ip.iitm.ac.in TTO page (when listing has an idf_url in
    iitm_patents.json; ~50-60% of seeded listings)
  - Fallback: Google Patents search by title + inventor

Behavior:
  - Polite delays + exponential backoff (configured in abstract_fetcher)
  - Resumable: writes scripts/backfill_abstracts.checkpoint.json after EVERY
    completed (success or failure) fetch. On --resume, skips listing_ids that
    are already in the checkpoint's `processed` set.
  - Audit log: scripts/backfill_abstracts.audit.jsonl with one row per fetch
    (listing_id, source, ok, error, latency_ms, abstract_len).
  - --limit N caps the run. --listing-ids "LIST-...,LIST-..." pins specific
    listings (e.g. our Mode A test case).
  - --dry-run does NOT write to the DB - useful for eyeballing samples before
    committing 898 fetches.

Usage:
    # First-pass sanity check: try 30 (incl. LIST-51D88207), no DB writes
    python scripts/backfill_abstracts.py --limit 30 \\
        --pin-listing-ids LIST-51D88207 --dry-run

    # Real run after eyeball:
    python scripts/backfill_abstracts.py --limit 30 \\
        --pin-listing-ids LIST-51D88207

    # Continue from checkpoint:
    python scripts/backfill_abstracts.py --resume
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"C:\sl\spkgs")    # heavy deps live here on this dev box

from collabv.abstract_fetcher import AbstractFetcher                # noqa: E402
from collabv import marketplace_db as mdb                            # noqa: E402

DB_PATH        = str(ROOT / "collabv_data.db")
PATENTS_FILE   = ROOT / "iitm_patents.json"
CHECKPOINT_FILE = Path(__file__).parent / "backfill_abstracts.checkpoint.json"
AUDIT_LOG      = Path(__file__).parent / "backfill_abstracts.audit.jsonl"


def _load_idf_url_map() -> Dict[str, Optional[str]]:
    """Build patent_number -> idf_url lookup from the raw IITM feed.

    patent_listings doesn't have an idf_url column (we didn't add one in the
    Alembic schema), so we look it up here by matching patent_number against
    iitm_patents.json's idf_code. The IITM feed's patent_number field IS
    its idf_code (str), so this is a direct equality match.
    """
    if not PATENTS_FILE.exists():
        return {}
    patents = json.loads(PATENTS_FILE.read_text(encoding="utf-8"))
    return {str(p.get("patent_number", "")): p.get("idf_url") for p in patents}


def _load_checkpoint() -> Dict[str, Any]:
    if not CHECKPOINT_FILE.exists():
        return {"processed": [], "successful": 0, "failed": 0,
                "started_at": None, "last_updated_at": None}
    return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))


def _save_checkpoint(checkpoint: Dict[str, Any]) -> None:
    checkpoint["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    CHECKPOINT_FILE.write_text(json.dumps(checkpoint, indent=2),
                               encoding="utf-8")


def _audit(rec: Dict[str, Any]) -> None:
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _select_listings_to_process(
    limit: int,
    pin_ids: Set[str],
    skip_ids: Set[str],
    db_path: str,
) -> List[Dict[str, Any]]:
    """Return the listings to backfill. Pinned IDs first, then up to
    `limit` more drafts without abstracts (preferring those with idf_url
    available since they have the highest success probability)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Pull every draft without a usable abstract.
        rows = conn.execute("""
            SELECT * FROM patent_listings
            WHERE status='draft'
              AND (abstract IS NULL OR abstract = '' OR length(abstract) < 50)
        """).fetchall()
    finally:
        conn.close()
    rows = [dict(r) for r in rows if r["listing_id"] not in skip_ids]

    pinned   = [r for r in rows if r["listing_id"] in pin_ids]
    others   = [r for r in rows if r["listing_id"] not in pin_ids]

    keep = pinned[:]
    remaining = max(limit - len(pinned), 0)
    keep.extend(others[:remaining])
    return keep


async def _process(
    listings: List[Dict[str, Any]],
    idf_url_map: Dict[str, Optional[str]],
    dry_run: bool,
    db_path: str,
) -> Dict[str, Any]:
    checkpoint = _load_checkpoint()
    if checkpoint.get("started_at") is None:
        checkpoint["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    processed_set: Set[str] = set(checkpoint.get("processed", []))

    samples: List[Dict[str, Any]] = []
    by_source: Counter = Counter()
    by_outcome: Counter = Counter()
    errors: List[str] = []

    async with AbstractFetcher() as fetcher:
        for i, listing in enumerate(listings, 1):
            lid = listing["listing_id"]
            patent_number = str(listing.get("patent_number") or "")
            idf_url = idf_url_map.get(patent_number)
            inventors_raw = listing.get("inventor_names") or "[]"
            try:
                inventors = json.loads(inventors_raw) if isinstance(inventors_raw, str) else inventors_raw
            except (json.JSONDecodeError, TypeError):
                inventors = []

            print(f"  [{i:>3d}/{len(listings)}] {lid}  "
                  f"{'idf_url' if idf_url else 'no idf_url, will try Google Patents'}  "
                  f"title: {listing['title'][:60]}...")
            result = await fetcher.fetch(
                title=listing.get("title", ""),
                idf_url=idf_url,
                inventor_name=inventors[0] if inventors else None,
                patent_number=patent_number,
            )
            by_source[result.source] += 1
            by_outcome["ok" if result.ok else "fail"] += 1

            # Record sample for the eyeball view
            samples.append({
                "listing_id": lid,
                "title": listing["title"],
                "idf_url": idf_url,
                "source": result.source,
                "ok": result.ok,
                "abstract_len": len(result.abstract or ""),
                "abstract_preview": (result.abstract or "")[:400],
                "error": result.error,
                "latency_ms": round(result.latency_ms, 0),
            })

            # Audit log
            _audit({
                "listing_id": lid, "patent_number": patent_number,
                "source": result.source, "ok": result.ok,
                "abstract_len": len(result.abstract or ""),
                "error": result.error, "latency_ms": result.latency_ms,
                "ts": time.time(),
            })

            # Write to DB unless dry-run
            if result.ok and not dry_run:
                conn = sqlite3.connect(db_path)
                conn.execute(
                    """UPDATE patent_listings
                       SET abstract = ?, abstract_source = ?, updated_at = ?
                       WHERE listing_id = ?""",
                    (result.abstract, result.source, time.time(), lid),
                )
                conn.commit()
                conn.close()
            if not result.ok and result.error:
                errors.append(f"{lid}: {result.error}")

            processed_set.add(lid)
            checkpoint["processed"] = sorted(processed_set)
            checkpoint["successful"] = by_outcome["ok"]
            checkpoint["failed"]     = by_outcome["fail"]
            if not dry_run:
                _save_checkpoint(checkpoint)

    return {
        "samples": samples,
        "by_source": dict(by_source),
        "by_outcome": dict(by_outcome),
        "errors": errors[:20],   # truncate for display
        "processed_total_in_checkpoint": len(processed_set),
    }


def _show_summary(summary: Dict[str, Any], n_to_preview: int = 3) -> None:
    print()
    print("=" * 78)
    print("Backfill summary")
    print("=" * 78)
    print(f"  by_outcome  : {summary['by_outcome']}")
    print(f"  by_source   : {summary['by_source']}")
    print(f"  processed   : {summary['processed_total_in_checkpoint']}")
    if summary["errors"]:
        print(f"  errors      : {len(summary['errors'])} (first 5)")
        for e in summary["errors"][:5]:
            print(f"    - {e}")
    print()
    # Show first n_to_preview successful fetches in full detail
    ok_samples = [s for s in summary["samples"] if s["ok"]]
    print(f"=== First {min(n_to_preview, len(ok_samples))} successful abstracts ===")
    for s in ok_samples[:n_to_preview]:
        print()
        print(f"--- {s['listing_id']} via {s['source']} ({s['abstract_len']} chars, {s['latency_ms']:.0f}ms) ---")
        print(f"  Title  : {s['title']}")
        if s['idf_url']:
            print(f"  idf_url: {s['idf_url']}")
        print(f"  Abstract preview (first 400 chars):")
        print(f"    {s['abstract_preview']}")
    if not ok_samples:
        print("  (No successful fetches to preview)")
    fail_samples = [s for s in summary["samples"] if not s["ok"]]
    if fail_samples:
        print()
        print(f"=== Sample failures (first 5) ===")
        for s in fail_samples[:5]:
            print(f"  {s['listing_id']}  source={s['source']}  reason={s['error']!r}")


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=30, help="max listings to process")
    p.add_argument("--pin-listing-ids", default="",
                   help="comma-separated listing_ids to force-include first")
    p.add_argument("--dry-run", action="store_true",
                   help="don't write abstracts to DB; just preview")
    p.add_argument("--resume", action="store_true",
                   help="skip listings already in the checkpoint")
    p.add_argument("--reset-checkpoint", action="store_true",
                   help="wipe checkpoint before starting")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("Checkpoint wiped.")

    idf_url_map = _load_idf_url_map()
    print(f"Loaded {sum(1 for v in idf_url_map.values() if v)} idf_urls from {PATENTS_FILE.name}")

    checkpoint = _load_checkpoint() if args.resume else {"processed": []}
    skip = set(checkpoint.get("processed", []))
    if args.resume:
        print(f"Resuming - {len(skip)} listings already processed")

    pin_ids = {p.strip() for p in args.pin_listing_ids.split(",") if p.strip()}
    listings = _select_listings_to_process(
        limit=args.limit, pin_ids=pin_ids, skip_ids=skip, db_path=DB_PATH,
    )
    print(f"Selected {len(listings)} listings to process "
          f"(pinned: {len(pin_ids)}, limit: {args.limit}, dry_run: {args.dry_run})")
    if not listings:
        print("Nothing to do.")
        return

    summary = asyncio.run(_process(listings, idf_url_map, args.dry_run, DB_PATH))
    _show_summary(summary, n_to_preview=3)

    if args.dry_run:
        print()
        print("DRY RUN - no abstracts were written to the DB.")
        print(f"Re-run without --dry-run to commit.")


if __name__ == "__main__":
    main()
