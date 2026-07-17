"""Self-contained CI fixture seed for scripts/smoke-test-production.sh.

Replaces the old seed_draft_listings.py / seed_patent_stub_profiles.py /
seed_test_accounts.py trio, which depended on retired static seed files
(iitm_professors_nlp.json, iitm_patents.json) that the live-data-only
architecture no longer loads. This script creates the exact minimal fixture
the smoke test needs, entirely through the same live DB-write paths a real
user's actions would go through:

  - Two professor_profiles rows (professor_id is a free-form string key —
    nothing requires it to come from the retired static directory):
      IITM-0143       profile_type=faculty      (the smoke test's default
                                                   SMOKE_INVENTOR_PROF_ID)
      STUB-RAMAPRABHU profile_type=patent_stub  (the smoke test's default
                                                   SMOKE_STUB_PROF_ID, drives
                                                   the STUB_REQUIRES_ADMIN_
                                                   ACTIVATION assertion)
  - 3 draft patent_listings owned by IITM-0143 (the smoke test consumes two:
    one for the M3 lifecycle-security checks, a second for the M5 buyer-flow
    activation) and 1 draft listing owned by STUB-RAMAPRABHU (M3's stub-gate
    check).
  - admin@example.com / inventor@example.com accounts, with inventor linked
    to IITM-0143 (mirrors real inventor-claim + admin-approval, but done
    directly since this is a system-seeded fixture, not a live user).

Buyer-profile seeding (the old seed_synthetic_buyers.py / seed_domain_
matched_buyers.py) is intentionally NOT reproduced here: the smoke test's
Mode B checks create their own buyer profile at runtime (see "Smoke Buyer"
in smoke-test-production.sh) and never query the buyer population, so no
pre-seeded buyers are required for any assertion to pass.

Idempotent: re-running replaces the two professor profiles and appends
fresh draft listings (harmless — the smoke test only needs "at least 2/1
draft" and cleans up by resetting activated listings back to draft, so the
pool only grows, never breaks).
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from collabv import marketplace_db as mdb
from collabv.patent_marketplace_db import save_professor_profile
from collabv.auth import (
    UserRegisterInput, create_user, authenticate, link_user_to_professor,
    init_auth_tables,
)
from fastapi import HTTPException

DB = str(ROOT / "collabv_data.db")

INVENTOR_PROF_ID = os.environ.get("SMOKE_INVENTOR_PROF_ID", "IITM-0143")
STUB_PROF_ID = os.environ.get("SMOKE_STUB_PROF_ID", "STUB-RAMAPRABHU")

ADMIN = {"email": "admin@example.com", "password": "AdminTest!23",
         "name": "Test Admin", "role": "admin"}
INVENTOR = {"email": "inventor@example.com", "password": "InventorTest!23",
            "name": "Test Inventor", "role": "professor_user"}


def seed_professors():
    save_professor_profile(INVENTOR_PROF_ID, {
        "professor_id": INVENTOR_PROF_ID,
        "name": "CI Fixture Faculty",
        "department": "Computer Science & Engineering",
        "profile_type": "faculty",
    }, db_path=DB)
    save_professor_profile(STUB_PROF_ID, {
        "professor_id": STUB_PROF_ID,
        "name": "CI Fixture Stub Professor",
        "department": "Mechanical Engineering",
        "profile_type": "patent_stub",
    }, db_path=DB)
    print(f"  professors: {INVENTOR_PROF_ID} (faculty), {STUB_PROF_ID} (patent_stub)")


def seed_listings():
    for i in range(3):
        mdb.save_listing({
            "professor_id": INVENTOR_PROF_ID,
            "title": f"CI Fixture Patent {i + 1}",
            "abstract": "Synthetic fixture listing created by scripts/seed_ci_fixtures.py for CI smoke testing.",
            "status": mdb.LISTING_DRAFT,
            "domain_tags": ["ci_fixture"],
            "industry_tags": ["ci_fixture"],
        }, db_path=DB)
    mdb.save_listing({
        "professor_id": STUB_PROF_ID,
        "title": "CI Fixture Stub Patent",
        "abstract": "Synthetic fixture listing created by scripts/seed_ci_fixtures.py for CI smoke testing.",
        "status": mdb.LISTING_DRAFT,
        "domain_tags": ["ci_fixture"],
        "industry_tags": ["ci_fixture"],
    }, db_path=DB)
    print(f"  listings: 3 draft(s) for {INVENTOR_PROF_ID}, 1 draft for {STUB_PROF_ID}")


def get_or_create_user(payload):
    init_auth_tables(DB)
    existing = authenticate(DB, payload["email"], payload["password"])
    if existing:
        return existing
    try:
        return create_user(DB, UserRegisterInput(
            email=payload["email"], password=payload["password"],
            name=payload["name"], role=payload["role"],
        ))
    except HTTPException as e:
        if e.status_code == 409:
            import bcrypt
            import sqlite3
            pw_hash = bcrypt.hashpw(payload["password"].encode(), bcrypt.gensalt()).decode()
            conn = sqlite3.connect(DB)
            try:
                conn.execute("UPDATE users SET password_hash=?, role=? WHERE email=?",
                             (pw_hash, payload["role"], payload["email"]))
                conn.commit()
            finally:
                conn.close()
            return authenticate(DB, payload["email"], payload["password"])
        raise


def seed_accounts():
    admin = get_or_create_user(ADMIN)
    inventor = get_or_create_user(INVENTOR)
    link_user_to_professor(DB, inventor.id, INVENTOR_PROF_ID)
    print(f"  accounts: {ADMIN['email']} (admin), {INVENTOR['email']} (professor_user, linked -> {INVENTOR_PROF_ID})")
    assert admin is not None


def main():
    print("Seeding CI marketplace-smoke fixtures...")
    seed_professors()
    seed_listings()
    seed_accounts()
    print("Done.")


if __name__ == "__main__":
    main()
