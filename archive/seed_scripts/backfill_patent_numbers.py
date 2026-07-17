"""
Extract the canonical Indian Patent Number from ip.iitm.ac.in TTO pages and
write it to patent_listings.indian_patent_number.

Why: the IITM TTO pages don't carry abstracts (we proved that earlier), but
they DO display the granted patent number in the page text (e.g.
"IITM IDF Ref 3015  IN IN 567476 Patent Granted"). That number is:
  - the canonical legal identifier (credibility for real buyers)
  - the lookup key for any future commercial-API integration (path 2)
  - free metadata - cost is one HTTP GET per listing we already had to do

This script is intentionally narrower than backfill_abstracts.py: it does
ONE source (the IITM TTO page), parses ONE field (the patent number), and
is resumable from a checkpoint.

Usage:
    python scripts/backfill_patent_numbers.py --limit 30 --dry-run   # preview
    python scripts/backfill_patent_numbers.py                        # commit 898
    python scripts/backfill_patent_numbers.py --resume               # continue
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, r"C:\sl\spkgs")

DB_PATH         = str(ROOT / "collabv_data.db")
PATENTS_FILE    = ROOT / "iitm_patents.json"
CHECKPOINT_FILE = Path(__file__).parent / "backfill_patent_numbers.checkpoint.json"
AUDIT_LOG       = Path(__file__).parent / "backfill_patent_numbers.audit.jsonl"


# Patterns we've observed in IITM TTO page text:
#   "IITM IDF Ref 3015  IN IN 567476 Patent Granted"
#   "IN 567476"
#   "IN-567476-A"
# The double-IN appears to be "country code IN" followed by "patent ID IN 567476".
# We accept any of: "IN IN \d+", "IN \d+", "IN-\d+-[A-Z0-9]+", "INNNNNNN" concatenated.
_PATENT_NUM_RE = re.compile(
    r"\b"
    r"IN[\s\-]*"           # country code, possibly hyphenated
    r"(?:IN[\s\-]*)?"      # optional repeat (the "IN IN" pattern)
    r"(\d{5,8})"           # digits we capture
    r"(?:[\s\-][A-Z]\d?)?" # optional kind code suffix like "A1" or "B2"
    r"\b",
    re.IGNORECASE,
)


async def _fetch_html(session, url: str) -> Optional[str]:
    """One HTTP GET with a 30s timeout. Returns None on error."""
    try:
        async with session.get(url) as r:
            if r.status != 200:
                return None
            return await r.text()
    except Exception:
        return None


def _extract_patent_number(html: str) -> Optional[str]:
    """Pull the most likely Indian patent number out of TTO HTML.

    Strategy: search for IN-style patterns in the visible text only. We
    prefer matches near the phrase 'Patent Granted' if there are multiples,
    since the page sometimes lists a related patent number elsewhere.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "meta", "link", "head"]):
        tag.decompose()
    text = soup.get_text("  ", strip=True)

    # Collect every IN-prefixed match. The regex itself enforces the "IN"
    # prefix so we can't accidentally pick up a bare IITM IDF Ref number
    # (which has no IN prefix on the page).
    matches: List[tuple[int, str]] = []
    for m in _PATENT_NUM_RE.finditer(text):
        num = m.group(1)
        if not num or len(num) < 5:
            continue
        matches.append((m.start(), f"IN {num}"))

    if not matches:
        return None

    # Pick the match nearest to "Patent Granted" if present
    granted_idx = text.lower().find("patent granted")
    if granted_idx >= 0:
        matches.sort(key=lambda x: abs(x[0] - granted_idx))

    return matches[0][1]


def _load_checkpoint() -> Dict[str, Any]:
    if not CHECKPOINT_FILE.exists():
        return {"processed": [], "successful": 0, "failed": 0,
                "started_at": None, "last_updated_at": None}
    return json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))


def _save_checkpoint(ckpt: Dict[str, Any]) -> None:
    ckpt["last_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    CHECKPOINT_FILE.write_text(json.dumps(ckpt, indent=2), encoding="utf-8")


def _audit(rec: Dict[str, Any]) -> None:
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _load_idf_url_map() -> Dict[str, Optional[str]]:
    if not PATENTS_FILE.exists():
        return {}
    patents = json.loads(PATENTS_FILE.read_text(encoding="utf-8"))
    return {str(p.get("patent_number", "")): p.get("idf_url") for p in patents}


def _select_targets(limit: int, skip: Set[str], db_path: str) -> List[Dict[str, Any]]:
    """Listings without a patent number yet, that have an idf_url to parse."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT * FROM patent_listings
               WHERE indian_patent_number IS NULL
                  OR indian_patent_number = ''
               ORDER BY created_at"""
        ).fetchall()
    finally:
        conn.close()
    rows = [dict(r) for r in rows if r["listing_id"] not in skip]
    return rows[:limit] if limit > 0 else rows


async def _run(listings: List[Dict[str, Any]], idf_url_map: Dict[str, Optional[str]],
               dry_run: bool, db_path: str, delay_sec: float = 2.0) -> Dict[str, Any]:
    import aiohttp

    ckpt = _load_checkpoint()
    if ckpt.get("started_at") is None:
        ckpt["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    processed: Set[str] = set(ckpt.get("processed", []))

    samples: List[Dict[str, Any]] = []
    extracted = 0
    no_url = 0
    no_match = 0
    http_fail = 0
    errors: List[str] = []

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/148.0.0.0 Safari/537.36"}
    last_call = 0.0

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers=headers,
    ) as session:
        for i, listing in enumerate(listings, 1):
            lid = listing["listing_id"]
            pn = str(listing.get("patent_number") or "")
            idf_url = idf_url_map.get(pn)

            sample: Dict[str, Any] = {
                "listing_id": lid,
                "title": (listing.get("title") or "")[:80],
                "patent_idf_code": pn,
                "idf_url": idf_url,
                "indian_patent_number": None,
                "result": "no_idf_url",
            }

            if not idf_url:
                no_url += 1
                processed.add(lid)
                samples.append(sample)
                _audit({"ts": time.time(), **sample})
                continue

            # Polite rate limit
            elapsed = time.time() - last_call
            if elapsed < delay_sec:
                await asyncio.sleep(delay_sec - elapsed)
            last_call = time.time()

            html = await _fetch_html(session, idf_url)
            if html is None:
                http_fail += 1
                sample["result"] = "http_fail"
                processed.add(lid)
                samples.append(sample)
                _audit({"ts": time.time(), **sample})
                if i <= 5 or i % 50 == 0:
                    print(f"  [{i:>3}/{len(listings)}] {lid}  http_fail")
                continue

            patent_num = _extract_patent_number(html)
            if not patent_num:
                no_match += 1
                sample["result"] = "no_match_in_html"
                processed.add(lid)
                samples.append(sample)
                _audit({"ts": time.time(), **sample})
                if i <= 5 or i % 50 == 0:
                    print(f"  [{i:>3}/{len(listings)}] {lid}  no patent number found")
                continue

            # Got one
            sample["indian_patent_number"] = patent_num
            sample["result"] = "extracted"
            extracted += 1
            if i <= 5 or i % 50 == 0:
                print(f"  [{i:>3}/{len(listings)}] {lid}  -> {patent_num}")

            if not dry_run:
                conn = sqlite3.connect(db_path)
                conn.execute(
                    "UPDATE patent_listings SET indian_patent_number=?, updated_at=? "
                    "WHERE listing_id=?",
                    (patent_num, time.time(), lid),
                )
                conn.commit()
                conn.close()

            processed.add(lid)
            samples.append(sample)
            _audit({"ts": time.time(), **sample})

            ckpt["processed"] = sorted(processed)
            ckpt["successful"] = extracted
            ckpt["failed"] = no_url + no_match + http_fail
            if not dry_run:
                _save_checkpoint(ckpt)

    return {
        "total_processed": len(listings),
        "extracted": extracted,
        "no_idf_url": no_url,
        "no_match_in_html": no_match,
        "http_fail": http_fail,
        "extraction_rate": (extracted / len(listings)) if listings else 0,
        "samples": samples,
    }


def _show(summary: Dict[str, Any], n_samples: int = 3) -> None:
    print()
    print("=" * 72)
    print("Extraction summary")
    print("=" * 72)
    print(f"  total processed       : {summary['total_processed']}")
    print(f"  extracted             : {summary['extracted']}")
    print(f"  no_idf_url            : {summary['no_idf_url']}")
    print(f"  no_match_in_html      : {summary['no_match_in_html']}")
    print(f"  http_fail             : {summary['http_fail']}")
    print(f"  extraction rate       : {summary['extraction_rate']:.1%}")
    print()
    ok = [s for s in summary["samples"] if s["result"] == "extracted"]
    if ok:
        print(f"=== First {min(n_samples, len(ok))} successful extractions ===")
        for s in ok[:n_samples]:
            print(f"  {s['listing_id']}  idf_code={s['patent_idf_code']:5}  "
                  f"-> {s['indian_patent_number']}")
            print(f"    title: {s['title']!r}")
            print(f"    url  : {s['idf_url']}")
            print()
    failed = [s for s in summary["samples"] if s["result"] != "extracted"]
    if failed:
        print(f"=== First 5 misses ===")
        for s in failed[:5]:
            print(f"  {s['listing_id']}  reason={s['result']:20}  "
                  f"idf_url={'yes' if s['idf_url'] else 'NO'}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reset-checkpoint", action="store_true")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="polite delay between IITM TTO requests (seconds)")
    args = parser.parse_args()

    if args.reset_checkpoint and CHECKPOINT_FILE.exists():
        CHECKPOINT_FILE.unlink()
        print("Checkpoint wiped.")

    idf_url_map = _load_idf_url_map()
    print(f"Loaded {sum(1 for v in idf_url_map.values() if v)} idf_urls "
          f"from {PATENTS_FILE.name}")

    ckpt = _load_checkpoint() if args.resume else {"processed": []}
    skip = set(ckpt.get("processed", []))
    if args.resume:
        print(f"Resuming - {len(skip)} listings already processed")

    targets = _select_targets(args.limit, skip, DB_PATH)
    print(f"Selected {len(targets)} listings to process "
          f"(limit: {args.limit}, dry_run: {args.dry_run}, delay: {args.delay}s)")

    if not targets:
        print("Nothing to do.")
        return

    summary = asyncio.run(_run(targets, idf_url_map, args.dry_run, DB_PATH, args.delay))
    _show(summary)

    if args.dry_run:
        print()
        print("DRY RUN - no patent numbers written to DB.")


if __name__ == "__main__":
    main()
