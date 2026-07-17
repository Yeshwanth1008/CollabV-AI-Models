"""Seed a test admin + test inventor for driving the activation flow through the UI.

Idempotent: re-running won't duplicate accounts. Prints credentials at the end.

NOTE on the claim flow: this script acts as a SYSTEM ACTOR — it sets
users.linked_professor_id directly via link_user_to_professor, bypassing the
admin-approval pending-claim path that real inventors must go through. That's
intentional for dev/test convenience: the test inventor is pre-approved so
local UI walks of the activation flow keep working without an admin click.
Real inventor onboarding in any non-dev environment must go through
POST /marketplace/inventor/claim + admin approval — never call this script
against a deployment that's open to public signup.
"""
from __future__ import annotations
import os, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from collabv.auth import (
    UserRegisterInput, create_user, authenticate, link_user_to_professor,
    init_auth_tables,
)
from fastapi import HTTPException

DB = str(ROOT / "collabv_data.db")

ADMIN = {"email": "admin@example.com", "password": "AdminTest!23",
         "name": "Test Admin", "role": "admin"}
INVENTOR = {"email": "inventor@example.com", "password": "InventorTest!23",
            "name": "Test Inventor", "role": "professor_user"}


def get_or_create(payload):
    init_auth_tables(DB)
    existing = authenticate(DB, payload["email"], payload["password"])
    if existing:
        return existing, False
    try:
        u = create_user(DB, UserRegisterInput(
            email=payload["email"], password=payload["password"],
            name=payload["name"], role=payload["role"],
        ))
        return u, True
    except HTTPException as e:
        if e.status_code == 409:
            # Account exists but with different password -> reset it
            import bcrypt
            pw_hash = bcrypt.hashpw(payload["password"].encode(),
                                    bcrypt.gensalt()).decode()
            conn = sqlite3.connect(DB)
            try:
                conn.execute("UPDATE users SET password_hash=?, role=? WHERE email=?",
                             (pw_hash, payload["role"], payload["email"]))
                conn.commit()
            finally:
                conn.close()
            existing = authenticate(DB, payload["email"], payload["password"])
            return existing, False
        raise


def pick_faculty_professor_with_drafts():
    """Find a real (non-stub) professor who owns the most draft listings."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    try:
        # We don't store profile_type in the listings table — peek at the
        # professors registry via the engine cache, which is hydrated on
        # startup. To stay independent of api.py boot, we pull it from
        # disk by scanning professor rows.
        # Fallback: pick any prof with the most drafts that doesn't start
        # with 'STUB-' (the stub convention in this DB).
        rows = conn.execute(
            """SELECT professor_id, COUNT(*) AS n FROM patent_listings
               WHERE status='draft' AND professor_id NOT LIKE 'STUB-%'
               GROUP BY professor_id ORDER BY n DESC LIMIT 1"""
        ).fetchone()
        return rows["professor_id"] if rows else None
    finally:
        conn.close()


def main():
    admin, admin_created = get_or_create(ADMIN)
    inventor, inv_created = get_or_create(INVENTOR)

    prof_id = pick_faculty_professor_with_drafts()
    if prof_id:
        link_user_to_professor(DB, inventor.id, prof_id)

    # Count drafts on that professor so the user knows what to expect in the UI.
    n = 0
    if prof_id:
        conn = sqlite3.connect(DB)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM patent_listings WHERE professor_id=? AND status='draft'",
                (prof_id,),
            ).fetchone()[0]
        finally:
            conn.close()

    print("=" * 72)
    print("Test accounts ready")
    print("=" * 72)
    print(f"Admin    {'(created)' if admin_created else '(reused)'}")
    print(f"  email     {ADMIN['email']}")
    print(f"  password  {ADMIN['password']}")
    print(f"  role      admin")
    print()
    print(f"Inventor {'(created)' if inv_created else '(reused)'}")
    print(f"  email     {INVENTOR['email']}")
    print(f"  password  {INVENTOR['password']}")
    print(f"  role      professor_user")
    print(f"  linked to professor_id = {prof_id}  ({n} draft listings on file)")
    print()
    print("Drive the flow:")
    print("  1. Sign in as inventor -> /marketplace/inventor")
    print("  2. Open a draft -> Submit for approval")
    print("  3. Sign out, sign in as admin -> /marketplace/admin")
    print("  4. Approve -> active")


if __name__ == "__main__":
    main()
