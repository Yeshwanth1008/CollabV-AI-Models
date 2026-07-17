"""
CollabV AI - Patent Marketplace persistence (audience profiles + offers)
============================================================================
Backs the Professor Dashboard's "sell your patent" feature: a professor can
market a patent to five audience types -

  company    - existing buyer_profiles (marketplace_db.py) - not duplicated here
  professor  - existing professor directory (matching_engine.professors),
               persisted here (professor_profiles) only for profiles
               created/edited via POST /professor/profile, since the base
               directory is a static JSON file never rewritten in place
  student    - NEW: student_profiles
  employee   - NEW: employee_profiles
  institute  - NEW: institute_profiles

...and record a "patent_offers" row when the professor directly offers/markets
a patent to a specific candidate in any of the five types, so both sides can
see sent/received offers and respond.

Also backs the Technology Transfer hub: negotiation_messages (chat threads
attached to a patent_offer or a listing_inquiry) and technology_requests
(a buyer posting "I need X" instead of just browsing).

Mirrors collabv/database.py style: sync sqlite3, snake_case tables, JSON blobs
in TEXT columns.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")

TARGET_TYPES = ("company", "student", "employee", "professor", "institute")

OFFER_STATUS_SENT = "sent"
OFFER_STATUS_VIEWED = "viewed"
OFFER_STATUS_ACCEPTED = "accepted"
OFFER_STATUS_DECLINED = "declined"


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_patent_marketplace_tables(db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS student_profiles (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            institute TEXT,
            field_of_study TEXT,
            skills TEXT,
            interests TEXT,
            research_areas TEXT,
            bio TEXT,
            education TEXT,
            projects TEXT,
            publications TEXT,
            certifications TEXT,
            internships TEXT,
            work_experience TEXT,
            startup_interests TEXT,
            career_goals TEXT,
            preferred_domains TEXT,
            achievements_soft_skills TEXT,
            resume_filename TEXT,
            resume_text TEXT,
            resume_file_path TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patent_transactions (
            transaction_id TEXT PRIMARY KEY,
            listing_id TEXT NOT NULL,
            patent_title TEXT,
            professor_id TEXT,
            professor_name TEXT,
            buyer_type TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            transaction_type TEXT NOT NULL,
            price REAL,
            license_expiry REAL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_transactions_buyer ON patent_transactions(buyer_type, buyer_id);
        CREATE INDEX IF NOT EXISTS idx_transactions_listing ON patent_transactions(listing_id);

        CREATE TABLE IF NOT EXISTS employee_profiles (
            user_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            company_name TEXT,
            job_title TEXT,
            industry TEXT,
            skills TEXT,
            interests TEXT,
            bio TEXT,
            education TEXT,
            projects TEXT,
            publications TEXT,
            certifications TEXT,
            internships TEXT,
            work_experience TEXT,
            industry_expertise TEXT,
            innovation_interests TEXT,
            startup_interests TEXT,
            career_goals TEXT,
            preferred_domains TEXT,
            achievements_soft_skills TEXT,
            resume_filename TEXT,
            resume_text TEXT,
            resume_file_path TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS institute_profiles (
            user_id TEXT PRIMARY KEY,
            institute_name TEXT NOT NULL,
            focus_areas TEXT,
            departments TEXT,
            collaboration_types TEXT,
            bio TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patent_offers (
            offer_id TEXT PRIMARY KEY,
            patent_id TEXT NOT NULL,
            patent_number TEXT,
            patent_title TEXT,
            professor_id TEXT NOT NULL,
            professor_name TEXT,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            target_name TEXT,
            match_score REAL,
            score_breakdown_json TEXT,
            reasons_json TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'sent',
            created_at REAL NOT NULL,
            responded_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_offers_professor ON patent_offers(professor_id);
        CREATE INDEX IF NOT EXISTS idx_offers_target ON patent_offers(target_type, target_id);

        CREATE TABLE IF NOT EXISTS listing_inquiries (
            inquiry_id TEXT PRIMARY KEY,
            listing_id TEXT NOT NULL,
            listing_title TEXT,
            professor_id TEXT,
            professor_name TEXT,
            buyer_type TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            buyer_name TEXT,
            message TEXT,
            match_score REAL,
            status TEXT NOT NULL DEFAULT 'sent',
            inquiry_type TEXT NOT NULL DEFAULT 'inquiry',
            created_at REAL NOT NULL,
            responded_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_listing_inquiries_listing ON listing_inquiries(listing_id);
        CREATE INDEX IF NOT EXISTS idx_listing_inquiries_professor ON listing_inquiries(professor_id);
        CREATE INDEX IF NOT EXISTS idx_listing_inquiries_buyer ON listing_inquiries(buyer_type, buyer_id);

        CREATE TABLE IF NOT EXISTS wishlist_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_type TEXT NOT NULL,
            buyer_id TEXT NOT NULL,
            listing_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            UNIQUE(buyer_type, buyer_id, listing_id)
        );

        CREATE INDEX IF NOT EXISTS idx_wishlist_buyer ON wishlist_items(buyer_type, buyer_id);

        CREATE TABLE IF NOT EXISTS negotiation_messages (
            message_id TEXT PRIMARY KEY,
            thread_type TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            sender_role TEXT NOT NULL,
            sender_id TEXT NOT NULL,
            sender_name TEXT,
            body TEXT,
            counter_price REAL,
            counter_terms TEXT,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_neg_messages_thread ON negotiation_messages(thread_type, thread_id);

        CREATE TABLE IF NOT EXISTS technology_requests (
            request_id TEXT PRIMARY KEY,
            requester_type TEXT NOT NULL,
            requester_id TEXT NOT NULL,
            requester_name TEXT,
            title TEXT NOT NULL,
            description TEXT,
            keywords TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_tech_requests_status ON technology_requests(status);
        CREATE INDEX IF NOT EXISTS idx_tech_requests_requester ON technology_requests(requester_type, requester_id);

        CREATE TABLE IF NOT EXISTS company_profiles (
            company_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            description TEXT,
            industry TEXT,
            business_domain TEXT,
            products_services TEXT,
            technologies_used TEXT,
            tech_stack TEXT,
            research_interests TEXT,
            business_objectives TEXT,
            focus_areas TEXT,
            keywords TEXT,
            market_segment TEXT,
            innovation_challenges TEXT,
            strategic_goals TEXT,
            existing_projects TEXT,
            preferred_collaboration_areas TEXT,
            company_size TEXT,
            category TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS professor_match_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id TEXT NOT NULL,
            professor_id TEXT NOT NULL,
            interaction_type TEXT NOT NULL,
            match_score REAL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_prof_interactions_company ON professor_match_interactions(company_id);
        CREATE INDEX IF NOT EXISTS idx_prof_interactions_professor ON professor_match_interactions(professor_id);

        CREATE TABLE IF NOT EXISTS match_interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_kind TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_id TEXT NOT NULL,
            interaction_type TEXT NOT NULL,
            match_score REAL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_match_interactions_source ON match_interactions(source_kind, source_id);
        CREATE INDEX IF NOT EXISTS idx_match_interactions_target ON match_interactions(target_kind, target_id);

        CREATE TABLE IF NOT EXISTS professor_profiles (
            professor_id TEXT PRIMARY KEY,
            data TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    # Idempotent ALTERs for DBs that pre-date these columns/tables. Additive
    # only - no data is lost on re-run.
    for sql in (
        "ALTER TABLE student_profiles ADD COLUMN education TEXT",
        "ALTER TABLE student_profiles ADD COLUMN projects TEXT",
        "ALTER TABLE student_profiles ADD COLUMN publications TEXT",
        "ALTER TABLE student_profiles ADD COLUMN certifications TEXT",
        "ALTER TABLE student_profiles ADD COLUMN internships TEXT",
        "ALTER TABLE student_profiles ADD COLUMN work_experience TEXT",
        "ALTER TABLE student_profiles ADD COLUMN startup_interests TEXT",
        "ALTER TABLE student_profiles ADD COLUMN career_goals TEXT",
        "ALTER TABLE student_profiles ADD COLUMN preferred_domains TEXT",
        "ALTER TABLE student_profiles ADD COLUMN achievements_soft_skills TEXT",
        "ALTER TABLE student_profiles ADD COLUMN resume_filename TEXT",
        "ALTER TABLE student_profiles ADD COLUMN resume_text TEXT",
        "ALTER TABLE student_profiles ADD COLUMN resume_file_path TEXT",
        "ALTER TABLE listing_inquiries ADD COLUMN inquiry_type TEXT NOT NULL DEFAULT 'inquiry'",
        "ALTER TABLE employee_profiles ADD COLUMN education TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN projects TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN publications TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN certifications TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN internships TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN work_experience TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN industry_expertise TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN innovation_interests TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN startup_interests TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN career_goals TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN preferred_domains TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN achievements_soft_skills TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN resume_filename TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN resume_text TEXT",
        "ALTER TABLE employee_profiles ADD COLUMN resume_file_path TEXT",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass    # column already exists
    conn.commit()
    conn.close()


# ─── Profile helpers ────────────────────────────────────────────────────────

def _upsert_profile(table: str, user_id: str, fields: Dict[str, Any], list_fields: List[str],
                     db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    now = time.time()
    existing = conn.execute(f"SELECT user_id FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
    row = dict(fields)
    for lf in list_fields:
        row[lf] = json.dumps(row.get(lf) or [])
    columns = list(row.keys())
    if existing:
        set_clause = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE {table} SET {set_clause}, updated_at = ? WHERE user_id = ?",
            [*row.values(), now, user_id],
        )
    else:
        columns_all = ["user_id", *columns, "created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns_all)
        conn.execute(
            f"INSERT INTO {table} ({', '.join(columns_all)}) VALUES ({placeholders})",
            [user_id, *row.values(), now, now],
        )
    conn.commit()
    conn.close()


def _row_to_profile(row: sqlite3.Row, list_fields: List[str]) -> Dict[str, Any]:
    d = dict(row)
    for lf in list_fields:
        if lf in d:
            d[lf] = json.loads(d[lf] or "[]")
    return d


STUDENT_LIST_FIELDS = [
    "skills", "interests", "research_areas", "education", "projects",
    "publications", "certifications", "internships", "work_experience",
    "startup_interests", "preferred_domains", "achievements_soft_skills",
]
EMPLOYEE_LIST_FIELDS = [
    "skills", "interests", "education", "projects", "publications",
    "certifications", "internships", "work_experience", "industry_expertise",
    "innovation_interests", "startup_interests", "preferred_domains",
    "achievements_soft_skills",
]
INSTITUTE_LIST_FIELDS = ["focus_areas", "departments", "collaboration_types"]


def save_student_profile(user_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    _upsert_profile("student_profiles", user_id, {
        "name": data.get("name", ""),
        "institute": data.get("institute", ""),
        "field_of_study": data.get("field_of_study", ""),
        "skills": data.get("skills", []),
        "interests": data.get("interests", []),
        "research_areas": data.get("research_areas", []),
        "bio": data.get("bio", ""),
        "education": data.get("education", []),
        "projects": data.get("projects", []),
        "publications": data.get("publications", []),
        "certifications": data.get("certifications", []),
        "internships": data.get("internships", []),
        "work_experience": data.get("work_experience", []),
        "startup_interests": data.get("startup_interests", []),
        "career_goals": data.get("career_goals", ""),
        "preferred_domains": data.get("preferred_domains", []),
        "achievements_soft_skills": data.get("achievements_soft_skills", []),
        "resume_filename": data.get("resume_filename", ""),
        "resume_text": data.get("resume_text", ""),
        "resume_file_path": data.get("resume_file_path", ""),
    }, STUDENT_LIST_FIELDS, db_path)


def save_employee_profile(user_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    _upsert_profile("employee_profiles", user_id, {
        "name": data.get("name", ""),
        "company_name": data.get("company_name", ""),
        "job_title": data.get("job_title", ""),
        "industry": data.get("industry", ""),
        "skills": data.get("skills", []),
        "interests": data.get("interests", []),
        "bio": data.get("bio", ""),
        "education": data.get("education", []),
        "projects": data.get("projects", []),
        "publications": data.get("publications", []),
        "certifications": data.get("certifications", []),
        "internships": data.get("internships", []),
        "work_experience": data.get("work_experience", []),
        "industry_expertise": data.get("industry_expertise", []),
        "innovation_interests": data.get("innovation_interests", []),
        "startup_interests": data.get("startup_interests", []),
        "career_goals": data.get("career_goals", ""),
        "preferred_domains": data.get("preferred_domains", []),
        "achievements_soft_skills": data.get("achievements_soft_skills", []),
        "resume_filename": data.get("resume_filename", ""),
        "resume_text": data.get("resume_text", ""),
        "resume_file_path": data.get("resume_file_path", ""),
    }, EMPLOYEE_LIST_FIELDS, db_path)


def save_institute_profile(user_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    _upsert_profile("institute_profiles", user_id, {
        "institute_name": data.get("institute_name", ""),
        "focus_areas": data.get("focus_areas", []),
        "departments": data.get("departments", []),
        "collaboration_types": data.get("collaboration_types", []),
        "bio": data.get("bio", ""),
    }, INSTITUTE_LIST_FIELDS, db_path)


def save_professor_profile(professor_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    """Persist a professor profile created/edited via POST /professor/profile.
    The base professor directory is a static JSON file loaded once at startup
    and never rewritten, so without this, professors added/edited through
    the API only ever lived in the in-memory engine.professors list/cache and
    vanished on the next restart. Stored as a single JSON blob (not shredded
    into columns like the other profile tables) since a professor record's
    shape is far richer/nested (patents list-of-dicts, domain_scores dict,
    nlp_tags, etc.) than the flat buyer-profile shapes above. On startup,
    list_professor_profiles() is merged back over the static JSON directory."""
    conn = _get_conn(db_path)
    now = time.time()
    payload = json.dumps(data)
    existing = conn.execute(
        "SELECT professor_id FROM professor_profiles WHERE professor_id = ?", (professor_id,),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE professor_profiles SET data = ?, updated_at = ? WHERE professor_id = ?",
            (payload, now, professor_id),
        )
    else:
        conn.execute(
            "INSERT INTO professor_profiles (professor_id, data, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (professor_id, payload, now, now),
        )
    conn.commit()
    conn.close()


def list_professor_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every professor profile created/edited via the API - merge this over
    the static JSON directory at startup so those changes survive a restart."""
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT data FROM professor_profiles").fetchall()
    conn.close()
    return [json.loads(r["data"]) for r in rows]


def delete_professor_profile(professor_id: str, db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.execute("DELETE FROM professor_profiles WHERE professor_id = ?", (professor_id,))
    conn.commit()
    conn.close()


def get_student_profile(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM student_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return _row_to_profile(row, STUDENT_LIST_FIELDS) if row else None


def get_employee_profile(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM employee_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return _row_to_profile(row, EMPLOYEE_LIST_FIELDS) if row else None


def get_institute_profile(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM institute_profiles WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return _row_to_profile(row, INSTITUTE_LIST_FIELDS) if row else None


def list_student_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM student_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [_row_to_profile(r, STUDENT_LIST_FIELDS) for r in rows]


def list_employee_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM employee_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [_row_to_profile(r, EMPLOYEE_LIST_FIELDS) for r in rows]


def list_institute_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM institute_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [_row_to_profile(r, INSTITUTE_LIST_FIELDS) for r in rows]


# ─── Offers ─────────────────────────────────────────────────────────────────

def create_offer(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    offer_id = f"OFFER-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO patent_offers
           (offer_id, patent_id, patent_number, patent_title, professor_id,
            professor_name, target_type, target_id, target_name, match_score,
            score_breakdown_json, reasons_json, message, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?)""",
        (
            offer_id,
            data["patent_id"],
            data.get("patent_number", ""),
            data.get("patent_title", ""),
            data["professor_id"],
            data.get("professor_name", ""),
            data["target_type"],
            data["target_id"],
            data.get("target_name", ""),
            data.get("match_score"),
            json.dumps(data.get("score_breakdown", {})),
            json.dumps(data.get("reasons", [])),
            data.get("message", ""),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return offer_id


def _row_to_offer(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "offer_id": row["offer_id"],
        "patent_id": row["patent_id"],
        "patent_number": row["patent_number"],
        "patent_title": row["patent_title"],
        "professor_id": row["professor_id"],
        "professor_name": row["professor_name"],
        "target_type": row["target_type"],
        "target_id": row["target_id"],
        "target_name": row["target_name"],
        "match_score": row["match_score"],
        "score_breakdown": json.loads(row["score_breakdown_json"] or "{}"),
        "reasons": json.loads(row["reasons_json"] or "[]"),
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
        "responded_at": row["responded_at"],
    }


def list_offers_sent(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM patent_offers WHERE professor_id = ? ORDER BY created_at DESC",
        (professor_id,),
    ).fetchall()
    conn.close()
    return [_row_to_offer(r) for r in rows]


def list_offers_received(target_type: str, target_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM patent_offers WHERE target_type = ? AND target_id = ?
           ORDER BY created_at DESC""",
        (target_type, target_id),
    ).fetchall()
    conn.close()
    return [_row_to_offer(r) for r in rows]


def respond_to_offer(offer_id: str, status: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE patent_offers SET status = ?, responded_at = ? WHERE offer_id = ?",
        (status, time.time(), offer_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM patent_offers WHERE offer_id = ?", (offer_id,)).fetchone()
    conn.close()
    return _row_to_offer(row) if row else None


def get_offer(offer_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM patent_offers WHERE offer_id = ?", (offer_id,)).fetchone()
    conn.close()
    return _row_to_offer(row) if row else None


# ─── Listing inquiries (buyer -> professor: "I want to buy/license this") ──

def create_listing_inquiry(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    inquiry_id = f"LINQ-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO listing_inquiries
           (inquiry_id, listing_id, listing_title, professor_id, professor_name,
            buyer_type, buyer_id, buyer_name, message, match_score, status, inquiry_type, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?)""",
        (
            inquiry_id,
            data["listing_id"],
            data.get("listing_title", ""),
            data.get("professor_id", ""),
            data.get("professor_name", ""),
            data["buyer_type"],
            data["buyer_id"],
            data.get("buyer_name", ""),
            data.get("message", ""),
            data.get("match_score"),
            data.get("inquiry_type", "inquiry"),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return inquiry_id


def _row_to_listing_inquiry(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "inquiry_id": row["inquiry_id"],
        "listing_id": row["listing_id"],
        "listing_title": row["listing_title"],
        "professor_id": row["professor_id"],
        "professor_name": row["professor_name"],
        "buyer_type": row["buyer_type"],
        "buyer_id": row["buyer_id"],
        "buyer_name": row["buyer_name"],
        "message": row["message"],
        "match_score": row["match_score"],
        "status": row["status"],
        "inquiry_type": row["inquiry_type"] if "inquiry_type" in row.keys() else "inquiry",
        "created_at": row["created_at"],
        "responded_at": row["responded_at"],
    }


def list_listing_inquiries_for_buyer(buyer_type: str, buyer_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM listing_inquiries WHERE buyer_type = ? AND buyer_id = ?
           ORDER BY created_at DESC""",
        (buyer_type, buyer_id),
    ).fetchall()
    conn.close()
    return [_row_to_listing_inquiry(r) for r in rows]


def list_listing_inquiries_for_professor(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM listing_inquiries WHERE professor_id = ?
           ORDER BY created_at DESC""",
        (professor_id,),
    ).fetchall()
    conn.close()
    return [_row_to_listing_inquiry(r) for r in rows]


def respond_to_listing_inquiry(inquiry_id: str, status: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE listing_inquiries SET status = ?, responded_at = ? WHERE inquiry_id = ?",
        (status, time.time(), inquiry_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM listing_inquiries WHERE inquiry_id = ?", (inquiry_id,)).fetchone()
    conn.close()
    return _row_to_listing_inquiry(row) if row else None


# ─── Purchase/license transactions ─────────────────────────────────────────
# No real payment processing exists on this platform - "buying" a patent
# means a professor accepting a buy/license inquiry, which is simulated
# here as a completed transaction record. Kept as its own table rather than
# routed through marketplace_db.transition_listing()'s state machine, which
# has no self-service buyer-driven path to LISTING_SOLD (only admin-
# initiated from active/paused) - touching it would risk the existing
# professor pause/withdraw/admin-approval flows.

def accept_purchase_inquiry(
    inquiry_id: str,
    price: Optional[float] = None,
    license_expiry: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Professor accepts a buy/license inquiry: marks it accepted and creates
    a patent_transactions record. A 'purchase' additionally retires the
    listing (transition_listing -> LISTING_SOLD); a 'license' does not,
    since a licensed patent can still be licensed to other buyers."""
    inquiry = respond_to_listing_inquiry(inquiry_id, "accepted", db_path)
    if not inquiry:
        return None

    transaction_type = "purchase" if inquiry.get("inquiry_type") == "purchase_request" else "license"
    transaction_id = f"TXN-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO patent_transactions
           (transaction_id, listing_id, patent_title, professor_id, professor_name,
            buyer_type, buyer_id, transaction_type, price, license_expiry, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)""",
        (
            transaction_id,
            inquiry["listing_id"],
            inquiry.get("listing_title", ""),
            inquiry.get("professor_id", ""),
            inquiry.get("professor_name", ""),
            inquiry["buyer_type"],
            inquiry["buyer_id"],
            transaction_type,
            price,
            license_expiry,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()

    if transaction_type == "purchase":
        from . import marketplace_db as mdb
        try:
            mdb.transition_listing(inquiry["listing_id"], mdb.LISTING_SOLD, actor_role="admin", db_path=db_path)
        except Exception:
            pass  # listing may already be in a terminal state; the transaction record is still valid

    return {"transaction_id": transaction_id, "transaction_type": transaction_type, "inquiry": inquiry}


def list_transactions_for_buyer(buyer_type: str, buyer_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM patent_transactions WHERE buyer_type = ? AND buyer_id = ? ORDER BY created_at DESC",
        (buyer_type, buyer_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Wishlist ───────────────────────────────────────────────────────────────

def add_wishlist_item(buyer_type: str, buyer_id: str, listing_id: str, db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT OR IGNORE INTO wishlist_items (buyer_type, buyer_id, listing_id, created_at)
           VALUES (?, ?, ?, ?)""",
        (buyer_type, buyer_id, listing_id, time.time()),
    )
    conn.commit()
    conn.close()


def remove_wishlist_item(buyer_type: str, buyer_id: str, listing_id: str, db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        "DELETE FROM wishlist_items WHERE buyer_type = ? AND buyer_id = ? AND listing_id = ?",
        (buyer_type, buyer_id, listing_id),
    )
    conn.commit()
    conn.close()


def list_wishlist_items(buyer_type: str, buyer_id: str, db_path: Optional[str] = None) -> List[str]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT listing_id FROM wishlist_items WHERE buyer_type = ? AND buyer_id = ? ORDER BY created_at DESC",
        (buyer_type, buyer_id),
    ).fetchall()
    conn.close()
    return [r["listing_id"] for r in rows]


# ─── Negotiation threads (chat, attached to an offer or a listing inquiry) ──

def send_message(
    thread_type: str, thread_id: str, sender_role: str, sender_id: str,
    sender_name: str, body: str,
    counter_price: Optional[float] = None,
    counter_terms: Optional[Dict[str, Any]] = None,
    db_path: Optional[str] = None,
) -> str:
    message_id = f"MSG-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO negotiation_messages
           (message_id, thread_type, thread_id, sender_role, sender_id, sender_name,
            body, counter_price, counter_terms, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            message_id, thread_type, thread_id, sender_role, sender_id, sender_name,
            body, counter_price, json.dumps(counter_terms) if counter_terms else None,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return message_id


def list_messages(thread_type: str, thread_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM negotiation_messages WHERE thread_type = ? AND thread_id = ?
           ORDER BY created_at ASC""",
        (thread_type, thread_id),
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["counter_terms"] = json.loads(d["counter_terms"]) if d["counter_terms"] else None
        out.append(d)
    return out


# ─── Technology requests (buyer posts "I need X", professors can respond) ──

def create_technology_request(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    request_id = f"TREQ-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO technology_requests
           (request_id, requester_type, requester_id, requester_name, title,
            description, keywords, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)""",
        (
            request_id,
            data["requester_type"],
            data["requester_id"],
            data.get("requester_name", ""),
            data["title"],
            data.get("description", ""),
            json.dumps(data.get("keywords", [])),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return request_id


def _row_to_tech_request(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["keywords"] = json.loads(d["keywords"] or "[]")
    return d


def list_technology_requests(status: Optional[str] = "open", db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    if status:
        rows = conn.execute(
            "SELECT * FROM technology_requests WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM technology_requests ORDER BY created_at DESC").fetchall()
    conn.close()
    return [_row_to_tech_request(r) for r in rows]


def get_technology_request(request_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM technology_requests WHERE request_id = ?", (request_id,)).fetchone()
    conn.close()
    return _row_to_tech_request(row) if row else None


def close_technology_request(request_id: str, status: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute("UPDATE technology_requests SET status = ? WHERE request_id = ?", (status, request_id))
    conn.commit()
    row = conn.execute("SELECT * FROM technology_requests WHERE request_id = ?", (request_id,)).fetchone()
    conn.close()
    return _row_to_tech_request(row) if row else None


# ─── Match interaction logging (views/saves/offers/inquiries/outcomes) ────
# Feeds "continuously improve recommendations" — recorded now, not yet fed
# back into scoring. That's a deliberate scope cut: real feedback-weighted
# re-ranking needs enough interaction volume to have signal, which doesn't
# exist yet. This just makes sure the data is captured from day one.
# (Table itself - match_interactions - is created in init_patent_marketplace_tables.)

MATCH_INTERACTION_TYPES = (
    "view", "save", "bookmark", "offer_sent", "offer_accepted", "offer_declined",
    "inquiry_sent", "collaboration_started", "licensing_request", "purchase_request",
    "collaboration_proposal", "technology_transfer_request",
)


# ─── Company profiles (Company Dashboard: matched alongside/instead of a ──
# ─── selected problem statement) ───────────────────────────────────────────

COMPANY_PROFILE_LIST_FIELDS = [
    "products_services", "technologies_used", "tech_stack", "research_interests",
    "focus_areas", "keywords", "existing_projects", "preferred_collaboration_areas",
]


def save_company_profile(company_id: str, data: Dict[str, Any], db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    now = time.time()
    fields = {
        "company_name": data.get("company_name", ""),
        "description": data.get("description", ""),
        "industry": data.get("industry", ""),
        "business_domain": data.get("business_domain", ""),
        "products_services": json.dumps(data.get("products_services") or []),
        "technologies_used": json.dumps(data.get("technologies_used") or []),
        "tech_stack": json.dumps(data.get("tech_stack") or []),
        "research_interests": json.dumps(data.get("research_interests") or []),
        "business_objectives": data.get("business_objectives", ""),
        "focus_areas": json.dumps(data.get("focus_areas") or []),
        "keywords": json.dumps(data.get("keywords") or []),
        "market_segment": data.get("market_segment", ""),
        "innovation_challenges": data.get("innovation_challenges", ""),
        "strategic_goals": data.get("strategic_goals", ""),
        "existing_projects": json.dumps(data.get("existing_projects") or []),
        "preferred_collaboration_areas": json.dumps(data.get("preferred_collaboration_areas") or []),
        "company_size": data.get("company_size", ""),
        "category": data.get("category", ""),
    }
    existing = conn.execute("SELECT company_id FROM company_profiles WHERE company_id = ?", (company_id,)).fetchone()
    if existing:
        set_clause = ", ".join(f"{c} = ?" for c in fields)
        conn.execute(
            f"UPDATE company_profiles SET {set_clause}, updated_at = ? WHERE company_id = ?",
            [*fields.values(), now, company_id],
        )
    else:
        columns = ["company_id", *fields.keys(), "created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO company_profiles ({', '.join(columns)}) VALUES ({placeholders})",
            [company_id, *fields.values(), now, now],
        )
    conn.commit()
    conn.close()


def _row_to_company_profile(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for lf in COMPANY_PROFILE_LIST_FIELDS:
        if lf in d:
            d[lf] = json.loads(d[lf] or "[]")
    return d


def get_company_profile(company_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM company_profiles WHERE company_id = ?", (company_id,)).fetchone()
    conn.close()
    return _row_to_company_profile(row) if row else None


def list_company_profiles(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM company_profiles ORDER BY updated_at DESC").fetchall()
    conn.close()
    return [_row_to_company_profile(r) for r in rows]


# ─── Professor match interaction logging (company-side) ───────────────────

def log_professor_interaction(
    company_id: str, professor_id: str, interaction_type: str,
    match_score: Optional[float] = None, db_path: Optional[str] = None,
) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO professor_match_interactions
           (company_id, professor_id, interaction_type, match_score, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (company_id, professor_id, interaction_type, match_score, time.time()),
    )
    conn.commit()
    conn.close()


def list_interactions_for_professor(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM professor_match_interactions WHERE professor_id = ? ORDER BY created_at DESC",
        (professor_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Unified match interaction logging (Engines 5+6 merged) ───────────────
# Any source (patent, or a professor/institute/company/student/employee
# buyer) interacting with any target (patent, listing, or an audience
# member): views, saves, bookmarks, offers, licensing/purchase requests,
# collaboration proposals, technology transfer requests. Logged separately
# from the (ephemeral, unpersisted) ranking itself - see matching_engine_5.

def log_match_interaction(
    source_kind: str, source_id: str, target_kind: str, target_id: str,
    interaction_type: str, match_score: Optional[float] = None,
    db_path: Optional[str] = None,
) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO match_interactions
           (source_kind, source_id, target_kind, target_id, interaction_type, match_score, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (source_kind, source_id, target_kind, target_id, interaction_type, match_score, time.time()),
    )
    conn.commit()
    conn.close()


def list_match_interactions_for_source(
    source_kind: str, source_id: str, db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM match_interactions
           WHERE source_kind = ? AND source_id = ? ORDER BY created_at DESC""",
        (source_kind, source_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_match_interactions_for_target(
    target_kind: str, target_id: str, db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM match_interactions WHERE target_kind = ? AND target_id = ? ORDER BY created_at DESC",
        (target_kind, target_id),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


__all__ = [
    "TARGET_TYPES",
    "init_patent_marketplace_tables",
    "save_student_profile", "get_student_profile", "list_student_profiles",
    "save_employee_profile", "get_employee_profile", "list_employee_profiles",
    "save_institute_profile", "get_institute_profile", "list_institute_profiles",
    "create_offer", "list_offers_sent", "list_offers_received",
    "respond_to_offer", "get_offer",
    "create_listing_inquiry", "list_listing_inquiries_for_buyer",
    "list_listing_inquiries_for_professor", "respond_to_listing_inquiry",
    "accept_purchase_inquiry", "list_transactions_for_buyer",
    "add_wishlist_item", "remove_wishlist_item", "list_wishlist_items",
    "send_message", "list_messages",
    "create_technology_request", "list_technology_requests",
    "get_technology_request", "close_technology_request",
    "MATCH_INTERACTION_TYPES", "log_match_interaction",
    "list_match_interactions_for_source", "list_match_interactions_for_target",
    "save_company_profile", "get_company_profile", "list_company_profiles",
    "log_professor_interaction", "list_interactions_for_professor",
]
