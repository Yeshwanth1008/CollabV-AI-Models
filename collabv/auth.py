"""
CollabV AI - JWT Authentication & Authorization
=================================================
Optional auth layer. Wire it into the FastAPI app by adding the
`require_user` / `require_role` dependencies on protected endpoints.

Schema additions (call init_auth_tables(db_path) at startup):

    users (
      id, email, password_hash, name, company_name, role, api_key, tier,
      created_at
    )

Endpoints to register on the app:
    POST /auth/register
    POST /auth/login         -> {access_token, refresh_token}
    POST /auth/refresh
    GET  /auth/me

Roles: admin | company_user | professor_user
Tiers: free | pro | enterprise  (used for rate limiting)
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field

# We import JWT/bcrypt lazily so the rest of the app keeps working if these
# aren't installed.

JWT_SECRET = os.environ.get("JWT_SECRET", "change-me-in-prod")
ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)

# ─── Role constants ────────────────────────────────────────────────────────
# The `users.role` column is a free-form String(32) so adding new roles requires
# no schema change. Use these constants everywhere instead of bare strings.
ROLE_ADMIN            = "admin"
ROLE_COMPANY_USER     = "company_user"      # legacy: original B2B buyer-side
ROLE_PROFESSOR_USER   = "professor_user"
ROLE_BUYER_USER       = "buyer_user"        # marketplace: enterprise patent buyer
ROLE_STUDENT_USER     = "student_user"      # marketplace: browse + inquire only
ROLE_EMPLOYEE_USER    = "employee_user"     # patent marketplace: company employee
ROLE_INSTITUTE_USER   = "institute_user"    # patent marketplace: educational institute
VALID_ROLES = {
    ROLE_ADMIN, ROLE_COMPANY_USER, ROLE_PROFESSOR_USER,
    ROLE_BUYER_USER, ROLE_STUDENT_USER, ROLE_EMPLOYEE_USER, ROLE_INSTITUTE_USER,
}


def is_student_user(user: Any) -> bool:
    """True if the user holds the student role. Accepts UserOut, dict, or
    string role — convenient for the marketplace rule layer."""
    if user is None:
        return False
    if isinstance(user, str):
        return user.lower() == ROLE_STUDENT_USER
    role = getattr(user, "role", None) if not isinstance(user, dict) else user.get("role")
    return (role or "").lower() == ROLE_STUDENT_USER

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


# ─── Models ─────────────────────────────────────────────────────────────────

class UserRegisterInput(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    name: str
    company_name: str = ""
    role: str = "company_user"


class UserLoginInput(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    company_name: str
    role: str
    tier: str
    api_key: str
    created_at: float


# ─── DB ─────────────────────────────────────────────────────────────────────

def init_auth_tables(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                company_name TEXT,
                role TEXT NOT NULL DEFAULT 'company_user',
                api_key TEXT UNIQUE NOT NULL,
                tier TEXT NOT NULL DEFAULT 'free',
                created_at REAL NOT NULL,
                linked_professor_id TEXT
            )
        """)
        # Idempotent ALTER for pre-existing DBs that don't have the column.
        try:
            conn.execute("ALTER TABLE users ADD COLUMN linked_professor_id TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
        conn.commit()
    finally:
        conn.close()


def link_user_to_professor(db_path: str, user_id: str, professor_id: str) -> bool:
    """Associate a logged-in user with a faculty professor profile.

    Used by POST /marketplace/inventor/claim so the marketplace layer can
    derive actor_role='inventor' for that user's professor's listings.

    Returns True if the link was set (or already matched), False if the user
    doesn't exist.
    """
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT linked_professor_id FROM users WHERE id = ?",
                           (user_id,)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE users SET linked_professor_id = ? WHERE id = ?",
                     (professor_id, user_id))
        conn.commit()
        return True
    finally:
        conn.close()


def get_user_link(db_path: str, user_id: str) -> Optional[str]:
    """Return the user's linked_professor_id (or None)."""
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT linked_professor_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


# ─── Crypto helpers ────────────────────────────────────────────────────────

def _hash_password(plaintext: str) -> str:
    import bcrypt
    return bcrypt.hashpw(plaintext.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plaintext: str, hashed: str) -> bool:
    import bcrypt
    try:
        return bcrypt.checkpw(plaintext.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def _encode_token(payload: Dict[str, Any], ttl: timedelta) -> str:
    import jwt
    body = {
        **payload,
        "iat": int(time.time()),
        "exp": int((datetime.now(timezone.utc) + ttl).timestamp()),
    }
    return jwt.encode(body, JWT_SECRET, algorithm="HS256")


def _decode_token(token: str) -> Dict[str, Any]:
    import jwt
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Invalid token: {e}")


# ─── User management ───────────────────────────────────────────────────────

def create_user(db_path: str, payload: UserRegisterInput) -> UserOut:
    init_auth_tables(db_path)
    uid = f"USR-{uuid.uuid4().hex[:10].upper()}"
    api_key = secrets.token_urlsafe(32)
    pw = _hash_password(payload.password)
    now = time.time()

    conn = sqlite3.connect(db_path)
    try:
        try:
            conn.execute(
                """INSERT INTO users
                   (id, email, password_hash, name, company_name, role, api_key, tier, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'free', ?)""",
                (uid, payload.email, pw, payload.name, payload.company_name or "",
                 payload.role, api_key, now),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Email already registered")
    finally:
        conn.close()

    return UserOut(
        id=uid, email=payload.email, name=payload.name,
        company_name=payload.company_name, role=payload.role,
        tier="free", api_key=api_key, created_at=now,
    )


def authenticate(db_path: str, email: str, password: str) -> Optional[UserOut]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    finally:
        conn.close()
    if not row or not _verify_password(password, row["password_hash"]):
        return None
    return UserOut(
        id=row["id"], email=row["email"], name=row["name"],
        company_name=row["company_name"] or "", role=row["role"],
        tier=row["tier"], api_key=row["api_key"], created_at=row["created_at"],
    )


def fetch_user(db_path: str, user_id: str) -> Optional[UserOut]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return UserOut(
        id=row["id"], email=row["email"], name=row["name"],
        company_name=row["company_name"] or "", role=row["role"],
        tier=row["tier"], api_key=row["api_key"], created_at=row["created_at"],
    )


def issue_tokens(user: UserOut) -> TokenResponse:
    access = _encode_token({"sub": user.id, "role": user.role, "tier": user.tier}, ACCESS_TOKEN_TTL)
    refresh = _encode_token({"sub": user.id, "type": "refresh"}, REFRESH_TOKEN_TTL)
    return TokenResponse(access_token=access, refresh_token=refresh)


# ─── FastAPI dependencies ──────────────────────────────────────────────────

def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[UserOut]:
    db_path = getattr(request.app.state, "db_path", None)
    if not token or not db_path:
        return None
    payload = _decode_token(token)
    return fetch_user(db_path, payload.get("sub", ""))


def require_user(user: Optional[UserOut] = Depends(get_current_user)) -> UserOut:
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user


def require_role(*roles: str):
    def _check(user: UserOut = Depends(require_user)) -> UserOut:
        if user.role not in roles:
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user
    return _check


# ─── Rate limiting (per-tier) ─────────────────────────────────────────────

TIER_LIMITS = {"free": 10, "pro": 100, "enterprise": 10_000}

# Per-month proposal quotas for inventor-initiated outreach (Mode A).
PROPOSAL_MONTHLY_QUOTA = {"free": 10, "pro": 100, "enterprise": 10_000}
_rate_buckets: Dict[str, Dict[str, int]] = {}  # day -> {user_id: count}


def enforce_daily_quota(user: UserOut) -> None:
    """Simple in-memory daily quota. For production use Redis."""
    day = time.strftime("%Y-%m-%d")
    bucket = _rate_buckets.setdefault(day, {})
    limit = TIER_LIMITS.get(user.tier, 10)
    count = bucket.get(user.id, 0)
    if count >= limit:
        raise HTTPException(429, f"Daily quota of {limit} match runs exceeded")
    bucket[user.id] = count + 1


__all__ = [
    "init_auth_tables", "create_user", "authenticate", "fetch_user",
    "issue_tokens", "get_current_user", "require_user", "require_role",
    "enforce_daily_quota", "UserOut", "TokenResponse",
    "UserRegisterInput", "UserLoginInput",
    # Role helpers (used by marketplace_rules)
    "ROLE_ADMIN", "ROLE_COMPANY_USER", "ROLE_PROFESSOR_USER",
    "ROLE_BUYER_USER", "ROLE_STUDENT_USER", "ROLE_EMPLOYEE_USER",
    "ROLE_INSTITUTE_USER", "VALID_ROLES",
    "is_student_user", "PROPOSAL_MONTHLY_QUOTA",
    "link_user_to_professor", "get_user_link",
]
