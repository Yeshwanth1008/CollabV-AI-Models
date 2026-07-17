"""
Seed synthetic buyer_profiles rows from 100_Companies_Collaboration_Schema.xlsx.

Per agreed decisions:
  - Don't pre-create REAL accounts (no real user signups inferred from XLSX).
  - DO create synthetic buyer profiles for offline eval + demos, flagged with
    is_synthetic=True so production rankings can filter them out.
  - Mode A (candidate buyers per patent) needs a buyer population to exist
    before real signups arrive - these synthetic rows are what unblocks Mode A
    testing.

For each XLSX row we create:
  1. A users row with:
       role         = "buyer_user"
       email        = "synthetic-{company_id}@collabv.local"
       company_name = the XLSX company name
  2. A buyer_profiles row with:
       user_id              -> the users row above
       org_name             <- XLSX 'Company Name'
       org_type             = derived ("enterprise" by default; "startup" if
                              company name contains "Startup"/"Labs"/"AI")
       industry             <- XLSX 'Industry Sector'
       industries_of_interest <- XLSX 'Industry Domain' tokens
       technical_areas      <- XLSX 'Technical Area' tokens
       use_cases            <- XLSX 'Project Description' + 'Challenges'
                              joined; at least 100 chars (the min our Pydantic
                              model enforces)
       tech_maturity_preference <- XLSX 'Research Level' -> early_stage /
                              mid_stage / proven
       budget_band          <- XLSX 'Budget' -> low/medium/high
       geographic_scope     <- XLSX 'Location' (or "India" by default)
       is_synthetic         = True

Idempotent: re-running deletes the previous synthetic rows first (matched by
the deterministic email pattern) so reseeding from an updated XLSX is safe.
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import collabv.database as db                # noqa: E402
import collabv.auth as auth                  # noqa: E402
import collabv.marketplace_db as mdb         # noqa: E402

DB_PATH = str(ROOT / "collabv_data.db")
XLSX_PATH = ROOT / "100_Companies_Collaboration_Schema.xlsx"


# ─── Field mappers ───────────────────────────────────────────────────────

def _safe_str(value: Any, default: str = "") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    s = str(value).strip()
    return s if s and s.lower() != "nan" else default


def _safe_list(value: Any) -> List[str]:
    s = _safe_str(value)
    if not s:
        return []
    items = []
    for part in re.split(r"[;,|/]+|\sand\s", s):
        part = part.strip(" .")
        if part:
            items.append(part)
    return items


def _infer_org_type(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("startup", "labs", "ai ", "ai)", "tech ", "studio")):
        return "startup"
    if any(k in n for k in ("ltd", "limited", "corp", "inc", "pvt")):
        return "enterprise"
    return "enterprise"


def _budget_band(raw: str) -> str:
    s = raw.lower()
    if any(k in s for k in ("low", "lakh", "20-50", "small")):
        return "low"
    if any(k in s for k in ("high", "crore", ">2", "enterprise")):
        return "high"
    return "medium"


def _maturity_pref(raw: str) -> str:
    s = raw.lower()
    if "basic" in s or "fundamental" in s or "research" in s:
        return "early_stage"
    if "applied" in s or "prototype" in s or "mid" in s:
        return "mid_stage"
    if "product" in s or "deployment" in s or "proven" in s:
        return "proven"
    return "mid_stage"


# ─── User-row helper (no Pydantic - we want to set is_synthetic-style data
# manually since users doesn't have that field) ──────────────────────────

def _ensure_user(db_path: str, company_id: str, company_name: str) -> str:
    """Create or fetch a deterministic synthetic user. Returns user_id.

    Email pattern (deterministic so reruns are idempotent):
        synthetic-{company_id-slug}@collabv.local
    """
    email = f"synthetic-{re.sub(r'[^A-Za-z0-9]', '-', company_id).lower()}@collabv.local"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if row:
            return row["id"]
        uid = f"USR-SYN-{uuid.uuid4().hex[:8].upper()}"
        api_key = uuid.uuid4().hex   # not a real key - just needs to satisfy UNIQUE
        # We don't use auth.create_user because it bcrypt-hashes a real password
        # and runs validation; we want a fast deterministic insert.
        conn.execute(
            """INSERT INTO users (id, email, password_hash, name, company_name,
                                  role, api_key, tier, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'free', ?)""",
            (uid, email, "SYNTHETIC-NOT-A-REAL-HASH", company_name,
             company_name, auth.ROLE_BUYER_USER, api_key, time.time()),
        )
        conn.commit()
        return uid
    finally:
        conn.close()


def _delete_existing_synthetic(db_path: str) -> int:
    """Wipe previous synthetic buyer rows + their users so reseeding is clean."""
    conn = sqlite3.connect(db_path)
    try:
        # Find existing synthetic users by the email pattern
        cur = conn.execute(
            "SELECT id FROM users WHERE email LIKE 'synthetic-%@collabv.local'"
        )
        user_ids = [r[0] for r in cur.fetchall()]
        if not user_ids:
            return 0
        placeholders = ",".join("?" for _ in user_ids)
        conn.execute(f"DELETE FROM buyer_profiles WHERE user_id IN ({placeholders})", user_ids)
        conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
        conn.commit()
        return len(user_ids)
    finally:
        conn.close()


# ─── Main ────────────────────────────────────────────────────────────────

def main() -> Dict[str, Any]:
    if not XLSX_PATH.exists():
        raise SystemExit(f"XLSX not found: {XLSX_PATH}")

    # Make sure both schemas exist
    db.init_db(DB_PATH)
    auth.init_auth_tables(DB_PATH)
    mdb.init_marketplace_tables(DB_PATH)

    # Idempotency: drop existing synthetic rows
    wiped = _delete_existing_synthetic(DB_PATH)
    print(f"Wiped {wiped} pre-existing synthetic user+buyer pairs")

    df = pd.read_excel(XLSX_PATH, sheet_name=0, header=2)
    print(f"Loaded {len(df)} companies from {XLSX_PATH.name}")

    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        company_id   = _safe_str(row.get("Company Id"))
        company_name = _safe_str(row.get("Company Name"))
        if not company_id or not company_name:
            skipped.append({"row": dict(row), "reason": "missing id or name"})
            continue

        industry = _safe_str(row.get("Industry Sector"))
        domain   = _safe_str(row.get("Industry Domain"))
        tech_areas = _safe_list(row.get("Technical Area"))
        if not tech_areas:
            tech_areas = _safe_list(row.get("Required Expertise"))
        if not tech_areas:
            skipped.append({"company_id": company_id, "reason": "no technical_areas"})
            continue

        project_desc = _safe_str(row.get("Project Description"))
        challenges   = _safe_str(row.get("Challenges"))
        use_cases = " ".join(p for p in (project_desc, challenges) if p).strip()
        if len(use_cases) < 100:
            # Pad with the technical context so we satisfy the Pydantic min=100
            pad = f" Industry focus: {industry or domain}. Target areas: {', '.join(tech_areas)}."
            use_cases = (use_cases + pad).strip()
        # Hard cap to 5000 (Pydantic max)
        use_cases = use_cases[:5000]

        # 1. Ensure user
        user_id = _ensure_user(DB_PATH, company_id, company_name)

        # 2. Insert buyer profile
        buyer_id = mdb.save_buyer({
            "user_id": user_id,
            "org_name": company_name,
            "org_type": _infer_org_type(company_name),
            "industry": industry or domain or "Unknown",
            "industries_of_interest": [domain] if domain else [],
            "technical_areas": tech_areas[:30],
            "use_cases": use_cases,
            "tech_maturity_preference": _maturity_pref(_safe_str(row.get("Research Level"))),
            "budget_band": _budget_band(_safe_str(row.get("Budget"))),
            "geographic_scope": [_safe_str(row.get("Location"), "India")],
            "seller_preferences": {
                "preferred_collaboration_type": _safe_str(row.get("Collaboration Type")),
                "problem_type": _safe_str(row.get("Problem Type")),
            },
            "is_synthetic": True,
        }, db_path=DB_PATH)

        created.append({
            "company_id": company_id,
            "user_id": user_id,
            "buyer_id": buyer_id,
            "org_name": company_name,
            "industry": industry,
            "n_tech_areas": len(tech_areas),
        })

    print(f"Created {len(created)} synthetic buyers, skipped {len(skipped)}")
    print()
    print("First 8 buyers:")
    for c in created[:8]:
        print(f"  {c['buyer_id']:18s}  {c['org_name'][:32]:32s}  "
              f"({c['industry'][:30]:30s}, {c['n_tech_areas']} tech areas)")

    return {
        "buyers_created": len(created),
        "buyers_skipped": len(skipped),
        "previous_synthetic_wiped": wiped,
        "db_path": DB_PATH,
    }


if __name__ == "__main__":
    summary = main()
    print()
    print(json.dumps(summary, indent=2))
