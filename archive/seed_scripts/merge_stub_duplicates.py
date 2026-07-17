"""
One-shot merge: 7 confirmed-duplicate patent_stub profiles -> existing faculty.

Background:
  Earlier today the interactive rescue script (scripts/rescue_patent_matches.py)
  merged 8 high-confidence inventor->professor variants by appending patents
  into the faculty's `.patents[]` JSON array. Each rescued patent was tagged
  `rescued_via: "<IITM feed name>"`.

  Later, seed_patent_stub_profiles.py ran the *un-updated* matcher and
  recreated 49 patent_stub profiles - including 7 stubs that are the very
  same name variants the rescue had already covered. This split the
  inventor's patents across two identities: faculty has the JSON patents,
  stub has the SQL listings.

This script undoes that regression by:
  1. For each of the 7 explicit (stub_feed_name -> canonical_faculty_name)
     pairs, find the stub professor (matched by `_iitm_feed_name`) and the
     canonical faculty profile.
  2. Reattribute every patent_listings row from stub_pid -> faculty_pid.
  3. Dedupe listings: if a listing with the same (patent_number, title)
     already exists for the faculty, the stub row is removed without
     creating a duplicate. (None expected today - the rescue only touched
     faculty.patents[] JSON, never created listings.)
  4. Delete the stub from iitm_professors_nlp.json (and the mirror file).
  5. Write a JSONL audit at scripts/merge_stub_duplicates.audit.jsonl.

Safety:
  - Backs up iitm_professors_nlp.json and the SQLite DB before any change.
  - Idempotent: re-running detects that a stub has already been deleted and
    skips that merge.
  - Hardcoded denylist on the 4 already-flagged false positives so even if
    they accidentally appear in the merge list, they are skipped.
  - Held: BASAVARAJA MADIVALA GURAPPA -> Basavaraj M Gurappa is NOT in the
    merge list. Human review pending.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PROFS_FILE         = ROOT / "iitm_professors_nlp.json"
PROFS_WITH_PATENTS = ROOT / "iitm_professors_with_patents.json"
DB_PATH            = ROOT / "collabv_data.db"
AUDIT_LOG          = Path(__file__).parent / "merge_stub_duplicates.audit.jsonl"


# ─── The exact 7 merges, explicit and reviewed ──────────────────────────
# Each entry: (IITM feed name on the stub, canonical faculty 'name' field)
MERGES: List[Tuple[str, str]] = [
    ("DILLIPKUMAR CHAND",        "Dillip Kumar Chand"),
    ("AMITAVA DAS GUPTA",        "Amitava Dasgupta"),
    ("SRINIVASA REDDY K",        "Srinivas Reddy K"),
    ("VENKATARATNAM G",          "Venkatarathnam G"),
    ("SAURAB SAXENA",            "Saurabh Saxena"),
    ("GANAPATHY KRISHNAMURTHI",  "Ganapathy Krishnamurthy"),
    ("HARIKUMAR K C",            "Hari Kumar K.C"),
]

# Defensive: anything matching this is NEVER merged even if it ever leaks
# into the merge list above. Mirrors the audit's flagged false positives
# + the held case.
DENYLIST_FACULTY_NAMES = {
    # surname-coincidence false positives
    "lakshmi priya subramanian", "rajesh g", "kasi viswanathan s", "anand k",
    # held for human review
    "basavaraj m gurappa",
}


def main() -> Dict[str, Any]:
    # 1. Back up everything we touch
    stamp = time.strftime("%Y%m%d-%H%M%S")
    profs_bak = PROFS_FILE.with_suffix(f".pre-merge.{stamp}.bak.json")
    db_bak = DB_PATH.with_suffix(f".pre-merge.{stamp}.bak.db")
    shutil.copy(PROFS_FILE, profs_bak)
    shutil.copy(DB_PATH, db_bak)
    if PROFS_WITH_PATENTS.exists():
        shutil.copy(PROFS_WITH_PATENTS,
                    PROFS_WITH_PATENTS.with_suffix(f".pre-merge.{stamp}.bak.json"))
    print(f"Backups:\n  {profs_bak}\n  {db_bak}")
    print()

    # 2. Load state
    professors = json.loads(PROFS_FILE.read_text(encoding="utf-8"))
    profs_by_name = {(p.get("name") or "").lower(): p for p in professors}
    stubs_by_feed_name = {
        (p.get("_iitm_feed_name") or "").upper(): p
        for p in professors
        if p.get("profile_type") == "patent_stub"
    }
    initial_stub_count = sum(1 for p in professors if p.get("profile_type") == "patent_stub")
    print(f"Initial state: {len(professors)} professors, {initial_stub_count} stubs")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    initial_total_listings = conn.execute(
        "SELECT COUNT(*) FROM patent_listings").fetchone()[0]
    print(f"               {initial_total_listings} patent_listings")
    print()

    # 3. Execute merges
    audit: List[Dict[str, Any]] = []
    summary = {
        "merges_attempted": len(MERGES),
        "merges_applied": 0,
        "merges_skipped_already_done": 0,
        "merges_skipped_denied": 0,
        "merges_skipped_not_found": 0,
        "listings_reattributed": 0,
        "listings_duplicate_removed": 0,
    }

    professor_ids_to_remove: set = set()

    for feed_name, faculty_name in MERGES:
        # Safety: denylist check
        if faculty_name.lower() in DENYLIST_FACULTY_NAMES:
            print(f"  [SKIP-DENY  ] {feed_name!r} -> {faculty_name!r} (denylisted)")
            summary["merges_skipped_denied"] += 1
            audit.append({"feed_name": feed_name, "faculty_name": faculty_name,
                          "result": "skipped_denylist", "ts": time.time()})
            continue

        stub = stubs_by_feed_name.get(feed_name.upper())
        if not stub:
            # Idempotent re-run: stub may have been deleted in a prior pass
            print(f"  [SKIP-DONE  ] {feed_name!r} stub not present (already merged?)")
            summary["merges_skipped_already_done"] += 1
            audit.append({"feed_name": feed_name, "faculty_name": faculty_name,
                          "result": "skipped_already_merged", "ts": time.time()})
            continue

        faculty = profs_by_name.get(faculty_name.lower())
        if not faculty:
            print(f"  [SKIP-MISS  ] {feed_name!r}: faculty {faculty_name!r} not found")
            summary["merges_skipped_not_found"] += 1
            audit.append({"feed_name": feed_name, "faculty_name": faculty_name,
                          "result": "skipped_faculty_not_found", "ts": time.time()})
            continue

        stub_pid = stub["professor_id"]
        fac_pid = faculty["professor_id"]
        # Reattribute listings - but dedupe against any pre-existing faculty listing
        # with the same (patent_number, title).
        existing_keys = {
            (r["patent_number"], r["title"])
            for r in conn.execute(
                "SELECT patent_number, title FROM patent_listings WHERE professor_id=?",
                (fac_pid,),
            ).fetchall()
        }
        stub_listings = conn.execute(
            "SELECT listing_id, patent_number, title FROM patent_listings WHERE professor_id=?",
            (stub_pid,),
        ).fetchall()

        reattributed = 0
        duplicates_dropped = 0
        for r in stub_listings:
            key = (r["patent_number"], r["title"])
            if key in existing_keys:
                # Faculty already has an identical listing - drop the stub's row
                conn.execute("DELETE FROM patent_listings WHERE listing_id=?", (r["listing_id"],))
                duplicates_dropped += 1
            else:
                conn.execute(
                    "UPDATE patent_listings SET professor_id=?, updated_at=? WHERE listing_id=?",
                    (fac_pid, time.time(), r["listing_id"]),
                )
                existing_keys.add(key)
                reattributed += 1

        professor_ids_to_remove.add(stub_pid)
        summary["merges_applied"] += 1
        summary["listings_reattributed"] += reattributed
        summary["listings_duplicate_removed"] += duplicates_dropped

        print(f"  [MERGE      ] {feed_name!r:30} -> {faculty_name!r:28} "
              f"({stub_pid:20} -> {fac_pid:12})  "
              f"+{reattributed:>3} listings reattributed, "
              f"{duplicates_dropped} duplicates dropped")
        audit.append({
            "feed_name": feed_name,
            "faculty_name": faculty_name,
            "stub_professor_id": stub_pid,
            "faculty_professor_id": fac_pid,
            "listings_reattributed": reattributed,
            "listings_duplicate_dropped": duplicates_dropped,
            "result": "merged",
            "ts": time.time(),
        })

    conn.commit()

    # 4. Remove the merged stubs from professors JSON
    if professor_ids_to_remove:
        new_professors = [p for p in professors
                          if p.get("professor_id") not in professor_ids_to_remove]
        PROFS_FILE.write_text(
            json.dumps(new_professors, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if PROFS_WITH_PATENTS.exists():
            PROFS_WITH_PATENTS.write_text(
                json.dumps(new_professors, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        print(f"\nRemoved {len(professor_ids_to_remove)} stubs from professors JSON")
    else:
        new_professors = professors
        print("\nNo stubs removed (all merges were skips)")

    # 5. Audit log
    with open(AUDIT_LOG, "a", encoding="utf-8") as f:
        for rec in audit:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"Audit: {AUDIT_LOG}")

    # 6. Verify: no orphaned listings, no duplicates
    final_stub_count = sum(1 for p in new_professors if p.get("profile_type") == "patent_stub")
    final_total_listings = conn.execute(
        "SELECT COUNT(*) FROM patent_listings").fetchone()[0]

    # Orphan check: every listing's professor_id must exist in the JSON
    valid_pids = {p.get("professor_id") for p in new_professors}
    orphans = [r["listing_id"] for r in conn.execute(
        "SELECT listing_id, professor_id FROM patent_listings").fetchall()
        if r["professor_id"] not in valid_pids]

    # Duplicate check: no (professor_id, patent_number, title) appears twice
    dup_rows = conn.execute("""
        SELECT professor_id, patent_number, title, COUNT(*) AS n
        FROM patent_listings
        GROUP BY professor_id, patent_number, title
        HAVING COUNT(*) > 1
    """).fetchall()

    conn.close()

    print()
    print("=" * 72)
    print("Verification")
    print("=" * 72)
    print(f"  Stubs       : {initial_stub_count} -> {final_stub_count}  "
          f"(delta {final_stub_count - initial_stub_count})")
    print(f"  Listings    : {initial_total_listings} -> {final_total_listings}  "
          f"(delta {final_total_listings - initial_total_listings})")
    print(f"  Reattributed: {summary['listings_reattributed']}")
    print(f"  Duplicates removed: {summary['listings_duplicate_removed']}")
    print(f"  Orphan listings: {len(orphans)}  {'<-- FAIL' if orphans else 'OK'}")
    print(f"  Duplicate (prof, patent_number, title) groups: {len(dup_rows)}  "
          f"{'<-- FAIL' if dup_rows else 'OK'}")
    if orphans:
        print(f"    first 5 orphan listing_ids: {orphans[:5]}")
    if dup_rows:
        print(f"    duplicates: {[dict(r) for r in dup_rows[:3]]}")
    print()
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    main()
