"""
CollabV AI - Marketplace persistence (SQLite parallel to db_postgres.py)
=========================================================================
Mirrors collabv/database.py style: sync sqlite3, snake_case tables, JSON
blobs in TEXT columns, embeddings stored as JSON arrays (no vector type in
SQLite). Same `_get_conn` / `init_marketplace_tables` pattern.

The corresponding async / Postgres path lives in db_postgres.py + the
0002_marketplace_schema.py Alembic migration. Both produce the same logical
schema described in the migration's docstring.

Lifecycle states (TEXT, not enum):
    draft           -> created by inventor, NOT publicly visible
    pending_approval-> inventor submitted for admin review
    active          -> approved by admin, publicly browsable
    paused          -> temporarily off-market
    sold            -> transaction completed (terminal)
    withdrawn       -> inventor removed (terminal)
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")


# Listing lifecycle states - module-level constants so callers reference these
# instead of magic strings.
LISTING_DRAFT            = "draft"
LISTING_PENDING_APPROVAL = "pending_approval"
LISTING_ACTIVE           = "active"
LISTING_PAUSED           = "paused"
LISTING_SOLD             = "sold"
LISTING_WITHDRAWN        = "withdrawn"

PUBLIC_LISTING_STATES = (LISTING_ACTIVE,)  # what guests can browse
SELLABLE_STATES      = (LISTING_ACTIVE,)


# ─── Lifecycle state machine + actor role gates ──────────────────────────
#
# Allowed transitions, keyed by (from_state, actor_role) -> set of valid
# target states. "inventor" here means the user who owns the listing
# (listing.professor_id == that user's linked professor record). "admin"
# can override most transitions (TTO surrogate). "system" is for the
# bulk-import seed only - never exposed to the API layer.
#
# CRITICAL: a stub-owned listing has NO "inventor" actor (the stub has no
# user account). For stub listings, the inventor role simply doesn't apply
# - only admin can transition. This is enforced in transition_listing()
# below by checking profile_type before consulting this table.

_ALLOWED_TRANSITIONS: Dict[tuple, set] = {
    # draft
    (LISTING_DRAFT, "inventor"): {LISTING_PENDING_APPROVAL, LISTING_WITHDRAWN},
    (LISTING_DRAFT, "admin"):    {LISTING_PENDING_APPROVAL, LISTING_ACTIVE,
                                  LISTING_WITHDRAWN},
    (LISTING_DRAFT, "system"):   {LISTING_DRAFT},  # idempotent seed re-write
    # pending_approval
    (LISTING_PENDING_APPROVAL, "inventor"): {LISTING_DRAFT, LISTING_WITHDRAWN},
    (LISTING_PENDING_APPROVAL, "admin"):    {LISTING_ACTIVE, LISTING_DRAFT,
                                             LISTING_WITHDRAWN},
    # active
    (LISTING_ACTIVE, "inventor"): {LISTING_PAUSED, LISTING_WITHDRAWN},
    (LISTING_ACTIVE, "admin"):    {LISTING_PAUSED, LISTING_SOLD, LISTING_WITHDRAWN},
    # paused
    (LISTING_PAUSED, "inventor"): {LISTING_ACTIVE, LISTING_WITHDRAWN},
    (LISTING_PAUSED, "admin"):    {LISTING_ACTIVE, LISTING_SOLD, LISTING_WITHDRAWN},
    # sold / withdrawn are terminal - no transitions out
    (LISTING_SOLD, "admin"):     set(),
    (LISTING_WITHDRAWN, "admin"): set(),
}


class InvalidLifecycleTransition(Exception):
    """Raised when transition_listing() rejects a state-machine move.

    The .code attribute is one of the strings below so the API layer can map
    to an ErrorCode without parsing the message.
    """
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def transition_listing(
    listing_id: str,
    target_status: str,
    actor_role: str,
    actor_user_id: Optional[str] = None,
    professor_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Safely transition a listing's status with full gating.

    Args:
        listing_id     : the listing to transition
        target_status  : desired next state (one of the LISTING_* constants)
        actor_role     : one of {"inventor", "admin", "system"}. "inventor"
                         means the caller is the owner of the listing's
                         professor profile (the API layer asserts this BEFORE
                         calling - here we just gate on owner_type).
        actor_user_id  : the human user performing the action (stamped on
                         approval). For 'system' callers, pass None.
        professor_lookup : optional {professor_id: prof_dict} so we can check
                           profile_type without re-reading the JSON.

    Returns:
        dict with {old_status, new_status, listing_id, listing_owner_type,
        activated_at, approved_by_user_id}

    Raises:
        InvalidLifecycleTransition with one of these codes:
          LISTING_NOT_FOUND
          LISTING_NOT_ACTIVATABLE       - state machine refused
          STUB_REQUIRES_ADMIN_ACTIVATION - stub-owned, non-admin tried to activate
    """
    listing = get_listing(listing_id, db_path=db_path)
    if not listing:
        raise InvalidLifecycleTransition(
            "LISTING_NOT_FOUND", f"Listing {listing_id} not found")

    current_status = listing.get("status") or LISTING_DRAFT
    professor_id = listing.get("professor_id")

    # Determine owner type. Default to 'faculty' if the lookup doesn't have
    # the prof - safer than failing open (we'd rather over-gate than under-gate).
    owner_type = "faculty"
    if professor_lookup and professor_id:
        prof = professor_lookup.get(professor_id) or {}
        owner_type = prof.get("profile_type") or "faculty"

    # ─── Gate 1: stub-owned listings require ADMIN for any state change ──
    # The stub has no user account, so an "inventor" path is meaningless.
    # The actual human inventor must be contacted out-of-band; admin/TTO
    # then performs the activation on their behalf.
    if owner_type == "patent_stub" and actor_role not in ("admin", "system"):
        raise InvalidLifecycleTransition(
            "STUB_REQUIRES_ADMIN_ACTIVATION",
            "This listing is owned by an auto-created inventor stub. "
            "Activation requires admin/TTO confirmation that the real "
            "inventor has consented out-of-band.",
        )

    # ─── Gate 2: state machine ────────────────────────────────────────────
    allowed = _ALLOWED_TRANSITIONS.get((current_status, actor_role), set())
    # Special case: setting the same status is a no-op (idempotent retries OK)
    if target_status == current_status:
        return {
            "listing_id": listing_id,
            "old_status": current_status,
            "new_status": target_status,
            "listing_owner_type": owner_type,
            "no_op": True,
        }
    if target_status not in allowed:
        raise InvalidLifecycleTransition(
            "LISTING_NOT_ACTIVATABLE",
            f"Cannot transition {current_status} -> {target_status} as "
            f"{actor_role!r}. Allowed: {sorted(allowed) or 'none'}.",
        )

    # ─── Apply ────────────────────────────────────────────────────────────
    update_listing_status(listing_id, target_status,
                          actor_user_id=actor_user_id, db_path=db_path)
    # Re-fetch for the response
    updated = get_listing(listing_id, db_path=db_path) or {}
    return {
        "listing_id": listing_id,
        "old_status": current_status,
        "new_status": target_status,
        "listing_owner_type": owner_type,
        "activated_at": updated.get("activated_at"),
        "approved_at": updated.get("approved_at"),
        "approved_by_user_id": updated.get("approved_by_user_id"),
        "no_op": False,
    }


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─── Schema bootstrap ─────────────────────────────────────────────────────

def init_marketplace_tables(db_path: Optional[str] = None) -> None:
    """Create all marketplace tables if absent. Idempotent.

    NOTE: SQLite doesn't have native vector or pgvector. Embeddings live in a
    TEXT column as JSON-encoded list[float] of length 384. That's fine at our
    scale (a few hundred listings); production switches to Postgres + pgvector
    via Alembic migration 0002 transparently.
    """
    conn = _get_conn(db_path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS patent_listings (
                listing_id TEXT PRIMARY KEY,
                professor_id TEXT NOT NULL,
                patent_number TEXT,
                indian_patent_number TEXT,           -- e.g. 'IN 567476' from TTO page
                title TEXT NOT NULL,
                abstract TEXT,
                abstract_status TEXT DEFAULT 'none', -- 'none'|'pasted'|'fetched'
                claims_text TEXT,
                inventor_names TEXT,                 -- JSON array
                granted_date TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                licensing_terms TEXT,                -- JSON
                asking_price_inr REAL,
                domain_tags TEXT,                    -- JSON array
                industry_tags TEXT,                  -- JSON array
                abstract_source TEXT DEFAULT 'unknown',
                activated_at REAL,
                approved_at REAL,
                approved_by_user_id TEXT,
                embedding TEXT,                      -- JSON array[float] of length 384
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_patent_listings_professor_id ON patent_listings(professor_id);
            CREATE INDEX IF NOT EXISTS ix_patent_listings_status ON patent_listings(status);
            CREATE INDEX IF NOT EXISTS ix_patent_listings_created_at ON patent_listings(created_at);

            CREATE TABLE IF NOT EXISTS buyer_profiles (
                buyer_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL UNIQUE,
                org_name TEXT NOT NULL,
                org_type TEXT,
                industry TEXT,
                industries_of_interest TEXT,         -- JSON array
                technical_areas TEXT,                -- JSON array
                use_cases TEXT,
                tech_maturity_preference TEXT,
                budget_band TEXT,
                geographic_scope TEXT,               -- JSON array
                seller_preferences TEXT,             -- JSON
                is_synthetic INTEGER NOT NULL DEFAULT 0,
                embedding TEXT,                      -- JSON array[float]
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_buyer_profiles_user_id ON buyer_profiles(user_id);
            CREATE INDEX IF NOT EXISTS ix_buyer_profiles_org_type ON buyer_profiles(org_type);
            CREATE INDEX IF NOT EXISTS ix_buyer_profiles_industry ON buyer_profiles(industry);
            CREATE INDEX IF NOT EXISTS ix_buyer_profiles_is_synthetic ON buyer_profiles(is_synthetic);

            CREATE TABLE IF NOT EXISTS marketplace_proposals (
                proposal_id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                inventor_id TEXT NOT NULL,
                proposal_text TEXT,
                match_score REAL,
                score_breakdown TEXT,                -- JSON
                explanation TEXT,                    -- JSON
                status TEXT NOT NULL DEFAULT 'sent',
                created_at REAL NOT NULL,
                responded_at REAL
            );
            CREATE INDEX IF NOT EXISTS ix_mp_proposals_listing_id ON marketplace_proposals(listing_id);
            CREATE INDEX IF NOT EXISTS ix_mp_proposals_buyer_id ON marketplace_proposals(buyer_id);
            CREATE INDEX IF NOT EXISTS ix_mp_proposals_inventor_id ON marketplace_proposals(inventor_id);
            CREATE INDEX IF NOT EXISTS ix_mp_proposals_status ON marketplace_proposals(status);

            CREATE TABLE IF NOT EXISTS marketplace_inquiries (
                inquiry_id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                buyer_id TEXT,
                user_id TEXT NOT NULL,
                message TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                match_score_at_inquiry REAL,
                created_at REAL NOT NULL,
                responded_at REAL
            );
            CREATE INDEX IF NOT EXISTS ix_mp_inquiries_listing_id ON marketplace_inquiries(listing_id);
            CREATE INDEX IF NOT EXISTS ix_mp_inquiries_buyer_id ON marketplace_inquiries(buyer_id);
            CREATE INDEX IF NOT EXISTS ix_mp_inquiries_user_id ON marketplace_inquiries(user_id);

            CREATE TABLE IF NOT EXISTS marketplace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                actor_user_id TEXT,
                actor_role TEXT,
                subject_listing_id TEXT,
                subject_buyer_id TEXT,
                match_score_at_event REAL,
                position_in_ranking INTEGER,
                query_hash TEXT,
                payload TEXT,                        -- JSON
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_mp_events_actor_user_id ON marketplace_events(actor_user_id);
            CREATE INDEX IF NOT EXISTS ix_mp_events_subject_listing_id ON marketplace_events(subject_listing_id);
            CREATE INDEX IF NOT EXISTS ix_mp_events_subject_buyer_id ON marketplace_events(subject_buyer_id);
            CREATE INDEX IF NOT EXISTS ix_mp_events_event_type ON marketplace_events(event_type);
            CREATE INDEX IF NOT EXISTS ix_mp_events_query_hash ON marketplace_events(query_hash);

            CREATE TABLE IF NOT EXISTS marketplace_explanations (
                cache_key TEXT PRIMARY KEY,
                mode TEXT NOT NULL,
                subject_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                explanation_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_mp_expl_subject_target ON marketplace_explanations(subject_id, target_id);
            CREATE INDEX IF NOT EXISTS ix_mp_expl_mode ON marketplace_explanations(mode);

            -- Faculty-profile claim requests. Replaces the prior self-link flow
            -- that any professor_user could exploit to claim another faculty's
            -- profile and activate their patents. A claim now starts as
            -- 'pending' and only an admin can flip it to 'approved'. On approval
            -- the api layer sets users.linked_professor_id.
            -- TODO(scale): admin approval is the manual interim mechanism.
            -- For automated scale, replace with one of:
            --   (a) email-domain match against the professor's contact record
            --   (b) one-time verification email to the on-file address
            --   (c) Institute SSO assertion
            CREATE TABLE IF NOT EXISTS professor_claims (
                claim_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                requested_professor_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',     -- pending|approved|rejected
                note TEXT,                                  -- inventor's justification (optional)
                reviewer_user_id TEXT,
                review_note TEXT,                           -- admin reason on reject
                created_at REAL NOT NULL,
                reviewed_at REAL
            );
            CREATE INDEX IF NOT EXISTS ix_prof_claims_user_id ON professor_claims(user_id);
            CREATE INDEX IF NOT EXISTS ix_prof_claims_status ON professor_claims(status);
            CREATE INDEX IF NOT EXISTS ix_prof_claims_requested ON professor_claims(requested_professor_id);
        """)
        # Idempotent ALTERs for DBs that pre-date the new columns. These are
        # additive only - no data is lost on re-run.
        for sql in (
            "ALTER TABLE patent_listings ADD COLUMN abstract_status TEXT DEFAULT 'none'",
            "ALTER TABLE patent_listings ADD COLUMN indian_patent_number TEXT",
        ):
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass    # column already exists
        conn.commit()
    finally:
        conn.close()


# ─── ID helpers (match existing conventions: PREFIX-XXXXXXXX uppercase hex) ─

def new_listing_id() -> str:   return f"LIST-{uuid.uuid4().hex[:8].upper()}"
def new_buyer_id() -> str:     return f"BUY-{uuid.uuid4().hex[:8].upper()}"
def new_proposal_id() -> str:  return f"PROP-{uuid.uuid4().hex[:8].upper()}"
def new_inquiry_id() -> str:   return f"INQ-{uuid.uuid4().hex[:8].upper()}"
def new_claim_id() -> str:     return f"CLM-{uuid.uuid4().hex[:8].upper()}"


# ─── professor_claims helpers ────────────────────────────────────────────

CLAIM_PENDING  = "pending"
CLAIM_APPROVED = "approved"
CLAIM_REJECTED = "rejected"


def create_claim(user_id: str, requested_professor_id: str,
                 note: Optional[str] = None,
                 db_path: Optional[str] = None) -> Dict[str, Any]:
    """Open a new pending claim. If the user already has an open pending claim
    or has been approved for the same professor_id, returns that existing row
    instead of creating a duplicate (idempotent).
    """
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM professor_claims
               WHERE user_id = ?
                 AND requested_professor_id = ?
                 AND status IN (?, ?)
               ORDER BY created_at DESC LIMIT 1""",
            (user_id, requested_professor_id, CLAIM_PENDING, CLAIM_APPROVED),
        ).fetchone()
        if row:
            return dict(row)
        claim_id = new_claim_id()
        now = time.time()
        conn.execute(
            """INSERT INTO professor_claims
               (claim_id, user_id, requested_professor_id, status, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (claim_id, user_id, requested_professor_id, CLAIM_PENDING, note, now),
        )
        conn.commit()
        return {"claim_id": claim_id, "user_id": user_id,
                "requested_professor_id": requested_professor_id,
                "status": CLAIM_PENDING, "note": note, "created_at": now}
    finally:
        conn.close()


def get_claim(claim_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM professor_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def latest_claim_for_user(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Most-recent claim row for this user across all statuses. Used by the
    inventor dashboard to render pending/rejected/no-claim branches.
    """
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            """SELECT * FROM professor_claims
               WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_pending_claims(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM professor_claims
               WHERE status = ? ORDER BY created_at ASC""",
            (CLAIM_PENDING,),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def review_claim(claim_id: str, *, approve: bool, reviewer_user_id: str,
                 review_note: Optional[str] = None,
                 db_path: Optional[str] = None) -> Dict[str, Any]:
    """Admin action: flip a pending claim to approved or rejected. Caller
    is responsible for the auth check (admin role) and for setting
    users.linked_professor_id when approve=True (that lives in auth.py).
    Raises ValueError if the claim isn't pending.
    """
    claim = get_claim(claim_id, db_path=db_path)
    if not claim:
        raise ValueError(f"claim not found: {claim_id}")
    if claim["status"] != CLAIM_PENDING:
        raise ValueError(f"claim {claim_id} is {claim['status']!r}, cannot re-review")
    new_status = CLAIM_APPROVED if approve else CLAIM_REJECTED
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """UPDATE professor_claims
               SET status = ?, reviewer_user_id = ?, review_note = ?, reviewed_at = ?
               WHERE claim_id = ?""",
            (new_status, reviewer_user_id, review_note, time.time(), claim_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {**claim, "status": new_status, "reviewer_user_id": reviewer_user_id,
            "review_note": review_note}


# ─── JSON column helpers ──────────────────────────────────────────────────

def _jdumps(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _jloads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _row_to_listing(row: sqlite3.Row) -> Dict[str, Any]:
    """Inflate JSON columns on a listing row."""
    d = dict(row)
    for k in ("inventor_names", "licensing_terms", "domain_tags", "industry_tags", "embedding"):
        if k in d:
            d[k] = _jloads(d[k])
    return d


def _row_to_buyer(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for k in ("industries_of_interest", "technical_areas", "geographic_scope",
              "seller_preferences", "embedding"):
        if k in d:
            d[k] = _jloads(d[k])
    if "is_synthetic" in d:
        d["is_synthetic"] = bool(d["is_synthetic"])
    return d


def _row_to_proposal(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for k in ("score_breakdown", "explanation"):
        if k in d:
            d[k] = _jloads(d[k])
    return d


def _row_to_event(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    if "payload" in d:
        d["payload"] = _jloads(d["payload"])
    return d


# ─── patent_listings CRUD ────────────────────────────────────────────────

def save_listing(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    """Insert or update a listing. Returns listing_id (generated if absent)."""
    listing_id = data.get("listing_id") or new_listing_id()
    now = time.time()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO patent_listings
               (listing_id, professor_id, patent_number, title, abstract,
                claims_text, inventor_names, granted_date, status,
                licensing_terms, asking_price_inr, domain_tags, industry_tags,
                abstract_source, abstract_status, indian_patent_number,
                activated_at, approved_at, approved_by_user_id,
                embedding, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM patent_listings WHERE listing_id = ?), ?),
                       ?)""",
            (
                listing_id,
                data.get("professor_id"),
                data.get("patent_number"),
                data.get("title", ""),
                data.get("abstract"),
                data.get("claims_text"),
                _jdumps(data.get("inventor_names")),
                data.get("granted_date"),
                data.get("status", LISTING_DRAFT),
                _jdumps(data.get("licensing_terms")),
                data.get("asking_price_inr"),
                _jdumps(data.get("domain_tags")),
                _jdumps(data.get("industry_tags")),
                data.get("abstract_source", "unknown"),
                data.get("abstract_status", "none"),
                data.get("indian_patent_number"),
                data.get("activated_at"),
                data.get("approved_at"),
                data.get("approved_by_user_id"),
                _jdumps(data.get("embedding")),
                listing_id, now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return listing_id


def get_listing(listing_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM patent_listings WHERE listing_id = ?", (listing_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_listing(row) if row else None


def list_listings_for_professor(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM patent_listings
               WHERE professor_id = ? ORDER BY created_at DESC""",
            (professor_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_listing(r) for r in rows]


def list_active_listings(limit: int = 1000, offset: int = 0,
                         db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """All publicly visible listings (status='active'). Used for embedding index build."""
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM patent_listings
               WHERE status = ?
               ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (LISTING_ACTIVE, limit, offset),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_listing(r) for r in rows]


def update_listing_status(listing_id: str, status: str,
                          actor_user_id: Optional[str] = None,
                          db_path: Optional[str] = None) -> None:
    """Transition a listing's status. Sets activated_at when going active for the
    first time, approved_at when approval transition happens.
    """
    now = time.time()
    conn = _get_conn(db_path)
    try:
        # If transitioning to active and never activated, stamp it.
        if status == LISTING_ACTIVE:
            conn.execute(
                """UPDATE patent_listings
                   SET status = ?, activated_at = COALESCE(activated_at, ?),
                       approved_at = COALESCE(approved_at, ?),
                       approved_by_user_id = COALESCE(approved_by_user_id, ?),
                       updated_at = ?
                   WHERE listing_id = ?""",
                (status, now, now, actor_user_id, now, listing_id),
            )
        else:
            conn.execute(
                "UPDATE patent_listings SET status = ?, updated_at = ? WHERE listing_id = ?",
                (status, now, listing_id),
            )
        conn.commit()
    finally:
        conn.close()


def update_listing_embedding(listing_id: str, embedding: List[float],
                             db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE patent_listings SET embedding = ?, updated_at = ? WHERE listing_id = ?",
            (_jdumps(embedding), time.time(), listing_id),
        )
        conn.commit()
    finally:
        conn.close()


# ─── buyer_profiles CRUD ─────────────────────────────────────────────────

def save_buyer(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    buyer_id = data.get("buyer_id") or new_buyer_id()
    now = time.time()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO buyer_profiles
               (buyer_id, user_id, org_name, org_type, industry,
                industries_of_interest, technical_areas, use_cases,
                tech_maturity_preference, budget_band, geographic_scope,
                seller_preferences, is_synthetic, embedding,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM buyer_profiles WHERE buyer_id = ?), ?),
                       ?)""",
            (
                buyer_id,
                data.get("user_id"),
                data.get("org_name", ""),
                data.get("org_type"),
                data.get("industry"),
                _jdumps(data.get("industries_of_interest")),
                _jdumps(data.get("technical_areas")),
                data.get("use_cases"),
                data.get("tech_maturity_preference"),
                data.get("budget_band"),
                _jdumps(data.get("geographic_scope")),
                _jdumps(data.get("seller_preferences")),
                1 if data.get("is_synthetic") else 0,
                _jdumps(data.get("embedding")),
                buyer_id, now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return buyer_id


def get_buyer(buyer_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM buyer_profiles WHERE buyer_id = ?", (buyer_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_buyer(row) if row else None


def get_buyer_by_user(user_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM buyer_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    finally:
        conn.close()
    return _row_to_buyer(row) if row else None


def list_buyers(include_synthetic: bool = True, limit: int = 1000,
                db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """All buyer profiles. Pass include_synthetic=False to exclude offline-eval
    seed buyers from real production rankings."""
    conn = _get_conn(db_path)
    try:
        if include_synthetic:
            rows = conn.execute(
                "SELECT * FROM buyer_profiles ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM buyer_profiles WHERE is_synthetic = 0
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    finally:
        conn.close()
    return [_row_to_buyer(r) for r in rows]


def update_buyer_embedding(buyer_id: str, embedding: List[float],
                           db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE buyer_profiles SET embedding = ?, updated_at = ? WHERE buyer_id = ?",
            (_jdumps(embedding), time.time(), buyer_id),
        )
        conn.commit()
    finally:
        conn.close()


# ─── proposals + inquiries ───────────────────────────────────────────────

def save_proposal(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    proposal_id = data.get("proposal_id") or new_proposal_id()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO marketplace_proposals
               (proposal_id, listing_id, buyer_id, inventor_id, proposal_text,
                match_score, score_breakdown, explanation, status,
                created_at, responded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM marketplace_proposals WHERE proposal_id = ?), ?),
                       ?)""",
            (
                proposal_id,
                data.get("listing_id"),
                data.get("buyer_id"),
                data.get("inventor_id"),
                data.get("proposal_text"),
                data.get("match_score"),
                _jdumps(data.get("score_breakdown")),
                _jdumps(data.get("explanation")),
                data.get("status", "sent"),
                proposal_id, time.time(),
                data.get("responded_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return proposal_id


def get_proposal(proposal_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_proposal(row) if row else None


def list_proposals_for_buyer(buyer_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM marketplace_proposals
               WHERE buyer_id = ? ORDER BY created_at DESC""",
            (buyer_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_proposal(r) for r in rows]


def list_proposals_for_inventor(inventor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM marketplace_proposals
               WHERE inventor_id = ? ORDER BY created_at DESC""",
            (inventor_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_proposal(r) for r in rows]


def save_inquiry(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    inquiry_id = data.get("inquiry_id") or new_inquiry_id()
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO marketplace_inquiries
               (inquiry_id, listing_id, buyer_id, user_id, message,
                status, match_score_at_inquiry, created_at, responded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?,
                       COALESCE((SELECT created_at FROM marketplace_inquiries WHERE inquiry_id = ?), ?),
                       ?)""",
            (
                inquiry_id,
                data.get("listing_id"),
                data.get("buyer_id"),
                data.get("user_id"),
                data.get("message"),
                data.get("status", "new"),
                data.get("match_score_at_inquiry"),
                inquiry_id, time.time(),
                data.get("responded_at"),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return inquiry_id


# ─── Inquiry listings / status transitions ───────────────────────────────

INQUIRY_STATUSES = ("new", "acknowledged", "accepted", "declined")


def get_inquiry(inquiry_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_inquiries WHERE inquiry_id = ?", (inquiry_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_inquiries_for_user(user_id: str,
                            db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """All inquiries this user SENT, regardless of buyer_profile (a user can
    inquire without a buyer profile — buyer_id ends up null in that case).
    """
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM marketplace_inquiries
               WHERE user_id = ?
               ORDER BY created_at DESC""", (user_id,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def list_inquiries_for_listings(listing_ids: List[str],
                                db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """All inquiries received on a set of listings — used by the inventor inbox
    to pull every inquiry across their own listings in one query.
    """
    if not listing_ids:
        return []
    placeholders = ",".join("?" for _ in listing_ids)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            f"""SELECT * FROM marketplace_inquiries
                WHERE listing_id IN ({placeholders})
                ORDER BY created_at DESC""", listing_ids
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def update_inquiry_status(inquiry_id: str, status: str,
                          db_path: Optional[str] = None) -> Dict[str, Any]:
    """Move an inquiry through its lifecycle. Caller MUST have validated that
    the actor is the inventor of the underlying listing (api layer's job).
    Returns the updated row.
    """
    if status not in INQUIRY_STATUSES:
        raise ValueError(f"invalid inquiry status: {status!r}")
    inq = get_inquiry(inquiry_id, db_path=db_path)
    if not inq:
        raise ValueError(f"inquiry not found: {inquiry_id}")
    now = time.time()
    # responded_at fires on the first non-'new' transition only.
    new_responded_at = inq.get("responded_at") or (now if status != "new" else None)
    conn = _get_conn(db_path)
    try:
        conn.execute(
            "UPDATE marketplace_inquiries SET status=?, responded_at=? WHERE inquiry_id=?",
            (status, new_responded_at, inquiry_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {**inq, "status": status, "responded_at": new_responded_at}


# ─── events ───────────────────────────────────────────────────────────────

def record_event(data: Dict[str, Any], db_path: Optional[str] = None) -> int:
    conn = _get_conn(db_path)
    try:
        cur = conn.execute(
            """INSERT INTO marketplace_events
               (event_type, actor_user_id, actor_role, subject_listing_id,
                subject_buyer_id, match_score_at_event, position_in_ranking,
                query_hash, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("event_type"),
                data.get("actor_user_id"),
                data.get("actor_role"),
                data.get("subject_listing_id"),
                data.get("subject_buyer_id"),
                data.get("match_score_at_event"),
                data.get("position_in_ranking"),
                data.get("query_hash"),
                _jdumps(data.get("payload")),
                time.time(),
            ),
        )
        conn.commit()
        return int(cur.lastrowid or 0)
    finally:
        conn.close()


def query_events_for_training(min_age_days: float = 0.0,
                              db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Pull events for offline LTR training. Phase 1 doesn't call this — it's a
    seam for Phase 3."""
    cutoff = time.time() - (min_age_days * 86400)
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM marketplace_events WHERE created_at <= ? ORDER BY created_at",
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_event(r) for r in rows]


# ─── explanation cache ───────────────────────────────────────────────────

def get_explanation(cache_key: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM marketplace_explanations WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    d = dict(row)
    d["explanation"] = _jloads(d.pop("explanation_json"))
    return d


def save_explanation(cache_key: str, mode: str, subject_id: str, target_id: str,
                     explanation: Dict[str, Any],
                     db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    try:
        conn.execute(
            """INSERT OR REPLACE INTO marketplace_explanations
               (cache_key, mode, subject_id, target_id, explanation_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cache_key, mode, subject_id, target_id, _jdumps(explanation), time.time()),
        )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "init_marketplace_tables",
    # Lifecycle constants
    "LISTING_DRAFT", "LISTING_PENDING_APPROVAL", "LISTING_ACTIVE",
    "LISTING_PAUSED", "LISTING_SOLD", "LISTING_WITHDRAWN",
    "PUBLIC_LISTING_STATES", "SELLABLE_STATES",
    # Lifecycle gate
    "transition_listing", "InvalidLifecycleTransition",
    # ID helpers
    "new_listing_id", "new_buyer_id", "new_proposal_id", "new_inquiry_id",
    # Listings
    "save_listing", "get_listing", "list_listings_for_professor",
    "list_active_listings", "update_listing_status", "update_listing_embedding",
    # Buyers
    "save_buyer", "get_buyer", "get_buyer_by_user", "list_buyers",
    "update_buyer_embedding",
    # Proposals + inquiries
    "save_proposal", "get_proposal", "list_proposals_for_buyer",
    "list_proposals_for_inventor", "save_inquiry",
    # Events + cache
    "record_event", "query_events_for_training",
    "get_explanation", "save_explanation",
]
