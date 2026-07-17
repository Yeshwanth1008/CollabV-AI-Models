"""
Bulk-import the 1,437 real IITM patents as patent_listings rows with
status='draft'.

CRITICAL: status='draft' means NOT browsable and NOT sellable. Explicit
activation by the inventor (or admin approval for them) is the consent event
that flips a listing to 'active'. This script never sets 'active'.

The seed exists so:
  - We have realistic test inventory for the engine, indexing, and frontend.
  - Inventors signing in to the production app can see their (draft) patents
    pre-populated and choose which to activate, rather than typing them in.

Rules applied while importing:
  - Only GRANTED patents are imported. Pending / published are skipped
    (you generally only license granted IP, plus only-granted is what
    Phase 1's reranker is calibrated for).
  - Each patent attaches to professor_profiles by professor_id, which exists
    for all inventors after seed_patent_stub_profiles.py runs.
  - Abstracts come from iitm_patents.json when present (rare). Otherwise
    abstract_source = 'iitm_feed_title_only' and Phase 2 will auto-fetch
    from Google Patents at activation time.
  - Domain + industry tags are inferred from the title using the same
    keyword taxonomy the engine uses.
  - Idempotent: re-runs detect existing listings by (professor_id,
    patent_number) and skip them. Listings that exist but in a non-draft
    state are NEVER touched.

After this runs, the engine sees the listings but they are still invisible
to guests (status != 'active') - safety property the user requested.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import collabv.database as db                # noqa: E402
import collabv.marketplace_db as mdb         # noqa: E402
import collabv.auth as auth                  # noqa: E402
from collabv.patent_scraper import _match_inventor   # noqa: E402

DB_PATH      = str(ROOT / "collabv_data.db")
PROFS_FILE   = ROOT / "iitm_professors_nlp.json"
PATENTS_FILE = ROOT / "iitm_patents.json"


# Same lightweight taxonomy used in seed_patent_stub_profiles
_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "materials":     ["alloy", "composite", "polymer", "ceramic", "metallurg",
                      "nanomaterial", "thin film", "coating"],
    "biotech":       ["enzyme", "protein", "biopolymer", "drug", "biomarker",
                      "vaccine", "antibody", "gene"],
    "chemicals":     ["catalyst", "polymerization", "synthesis", "reaction",
                      "separation"],
    "ai_ml":         ["machine learning", "deep learning", "neural", "ai ",
                      "classification", "detection"],
    "electronics":   ["semiconductor", "vlsi", "circuit", "antenna",
                      "rf ", "5g", "wireless", "transistor"],
    "energy":        ["solar", "battery", "fuel cell", "hydrogen",
                      "photovoltaic", "energy storage", "renewable"],
    "robotics":      ["robot", "manipulator", "actuator", "gripper",
                      "autonomous"],
    "sensors_iot":   ["sensor", "iot", "wearable", "device"],
    "optics":        ["optical", "photonic", "laser", "lens", "fiber"],
    "mechanical":    ["mechanism", "linkage", "manufacturing", "machining",
                      "structure", "additive"],
    "civil":         ["concrete", "structural", "seismic", "construction"],
    "healthcare":    ["medical", "diagnostic", "implant", "surgical",
                      "rehabilitation", "prosthet"],
    "water":         ["water", "desalin", "filtration", "purification"],
}

_INDUSTRY_KEYWORDS: Dict[str, List[str]] = {
    "healthcare":      ["medical", "diagnostic", "implant", "surgical",
                        "prosthet", "drug delivery"],
    "energy":          ["solar", "battery", "fuel cell", "energy"],
    "electronics":     ["semiconductor", "vlsi", "circuit", "transistor"],
    "automotive":      ["vehicle", "automotive", "engine", "powertrain"],
    "aerospace":       ["aerospace", "aircraft", "uav", "drone", "propulsion"],
    "water":           ["water", "desalin", "filtration", "wastewater"],
    "manufacturing":   ["manufacturing", "machining", "additive", "3d print"],
    "agritech":        ["agricultur", "crop", "irrigation", "soil"],
    "chemicals":       ["chemical", "catalyst", "polymer"],
}


def _infer_tags(title: str, abstract: str = "") -> tuple[List[str], List[str]]:
    text = (title + " " + abstract).lower()
    domains = sorted({d for d, kw in _DOMAIN_KEYWORDS.items()
                      if any(k in text for k in kw)})
    industries = sorted({i for i, kw in _INDUSTRY_KEYWORDS.items()
                         if any(k in text for k in kw)})
    return domains, industries


def _existing_listings_keys(conn: sqlite3.Connection) -> set:
    """Return set of (professor_id, patent_number) for listings already in DB."""
    return {
        (r[0], r[1]) for r in
        conn.execute("SELECT professor_id, patent_number FROM patent_listings").fetchall()
    }


def main() -> Dict[str, Any]:
    # Bootstrap schemas
    db.init_db(DB_PATH)
    auth.init_auth_tables(DB_PATH)
    mdb.init_marketplace_tables(DB_PATH)

    # Load professors (now includes patent_stub records) and patent feed
    professors = json.loads(PROFS_FILE.read_text(encoding="utf-8"))
    patents = json.loads(PATENTS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(professors)} professor records "
          f"({sum(1 for p in professors if p.get('profile_type') == 'patent_stub')} stubs)")
    print(f"Loaded {len(patents)} patents from {PATENTS_FILE.name}")

    # Pre-compute name -> idx lookup (the _match_inventor scans linearly, so we
    # cache its result per inventor name across all patents from that inventor)
    inventor_idx: Dict[str, Optional[int]] = {}

    conn = sqlite3.connect(DB_PATH)
    try:
        existing = _existing_listings_keys(conn)
    finally:
        conn.close()
    print(f"  {len(existing)} listings already exist - will skip duplicates")

    created = 0
    skipped_not_granted = 0
    skipped_no_match = 0
    skipped_duplicate = 0
    skipped_no_title = 0
    by_status: Counter = Counter()
    by_inventor_type: Counter = Counter()

    for pat in patents:
        status_raw = str(pat.get("status", "")).lower()
        by_status[status_raw] += 1
        if status_raw != "granted":
            skipped_not_granted += 1
            continue

        title = str(pat.get("title", "")).strip()
        if not title:
            skipped_no_title += 1
            continue

        inv_raw = (pat.get("inventors") or [""])[0].strip()
        if inv_raw not in inventor_idx:
            inventor_idx[inv_raw] = _match_inventor(inv_raw, professors)
        idx = inventor_idx[inv_raw]
        if idx is None:
            skipped_no_match += 1
            continue
        prof = professors[idx]
        prof_id = prof.get("professor_id")
        patent_number = str(pat.get("patent_number") or "")
        by_inventor_type[prof.get("profile_type") or "faculty"] += 1

        if (prof_id, patent_number) in existing:
            skipped_duplicate += 1
            continue

        abstract = str(pat.get("abstract") or "").strip()
        abstract_source = "iitm_feed" if abstract else "iitm_feed_title_only"

        domains, industries = _infer_tags(title, abstract)

        mdb.save_listing({
            "professor_id":   prof_id,
            "patent_number":  patent_number,
            "title":          title,
            "abstract":       abstract or None,
            "claims_text":    None,
            "inventor_names": pat.get("inventors") or [],
            "granted_date":   pat.get("filing_date") or str(pat.get("year") or ""),
            "status":         mdb.LISTING_DRAFT,
            "licensing_terms": {
                "type": "non_exclusive",      # default; inventor edits
                "geo_scope": ["IN", "global"],
            },
            "asking_price_inr": None,         # 0 = open to negotiation
            "domain_tags":    domains,
            "industry_tags":  industries,
            "abstract_source": abstract_source,
            # activated_at / approved_at intentionally left NULL - this listing
            # is NOT publicly visible and has NOT been activated.
        }, db_path=DB_PATH)
        existing.add((prof_id, patent_number))
        created += 1

    print()
    print("Import summary:")
    print(f"  Patents processed         : {len(patents)}")
    print(f"  Listings CREATED (draft)  : {created}")
    print(f"  Skipped - not granted     : {skipped_not_granted}")
    print(f"  Skipped - no name match   : {skipped_no_match}")
    print(f"  Skipped - duplicate       : {skipped_duplicate}")
    print(f"  Skipped - no title        : {skipped_no_title}")
    print()
    print("Patent statuses in feed:")
    for s, c in by_status.most_common():
        print(f"  {s:14s} {c}")
    print()
    print("Created listings by inventor type:")
    for t, c in by_inventor_type.most_common():
        print(f"  {t:14s} {c}")

    # Safety assertion
    conn = sqlite3.connect(DB_PATH)
    try:
        active_count = conn.execute(
            f"SELECT COUNT(*) FROM patent_listings WHERE status = '{mdb.LISTING_ACTIVE}'"
        ).fetchone()[0]
        draft_count = conn.execute(
            f"SELECT COUNT(*) FROM patent_listings WHERE status = '{mdb.LISTING_DRAFT}'"
        ).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM patent_listings").fetchone()[0]
    finally:
        conn.close()
    print()
    print(f"DB state after seed:")
    print(f"  total listings : {total_count}")
    print(f"  draft          : {draft_count}")
    print(f"  active         : {active_count}  <-- MUST be 0 (we never auto-publish)")
    if active_count != 0:
        raise SystemExit("SAFETY CHECK FAILED: active listings created by seed - aborting")
    print(f"  SAFETY: no real patent was auto-published. OK.")

    return {
        "patents_processed": len(patents),
        "listings_created":  created,
        "skipped_not_granted": skipped_not_granted,
        "skipped_no_match":    skipped_no_match,
        "skipped_duplicate":   skipped_duplicate,
        "active_after_seed":   active_count,
        "draft_after_seed":    draft_count,
    }


if __name__ == "__main__":
    summary = main()
    print()
    print(json.dumps(summary, indent=2))
