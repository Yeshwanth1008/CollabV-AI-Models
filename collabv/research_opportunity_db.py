"""
CollabV AI - Matching Engine 8 persistence: Research Opportunities
=====================================================================
Backs the Student Dashboard's "AI Matching Engine 8" tab and the Professor
Dashboard's ranked-candidates section: professors post research
opportunities (PhD/Master's positions, internships, RA roles, fellowships,
etc.), students get AI-ranked matches, and professors get AI-ranked
candidate students per opportunity they posted.

Mirrors collabv/job_matching_db.py's style (_get_conn/idempotent
executescript/upsert-by-primary-key), extended with one thing the job
engine never needed: research_opportunity_matches is read from BOTH
directions (a student's ranked opportunity list, and a professor's ranked
candidate list for one opportunity), so it's indexed both ways.

"Express Interest" (not "Apply") is the student-initiated action - a
professor-run research opportunity is typically pre-screened
conversationally rather than a formal HR application pipeline - but the
table is otherwise structurally identical to job_applications (idempotent
via UNIQUE(student_id, opportunity_id)).
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")

OPPORTUNITY_TYPES = (
    "research_internship", "masters", "phd", "postdoctoral", "research_assistant",
    "thesis_dissertation", "lab_position", "collaborative_project",
    "visiting_researcher", "fellowship", "summer_winter_program", "other",
)


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_research_opportunity_tables(db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS research_opportunities (
            opportunity_id TEXT PRIMARY KEY,
            professor_id TEXT NOT NULL,
            professor_name TEXT NOT NULL,
            department TEXT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            opportunity_type TEXT NOT NULL DEFAULT 'research_internship',
            degree_level TEXT,
            research_areas TEXT,
            required_skills TEXT,
            preferred_skills TEXT,
            required_qualifications TEXT,
            preferred_qualifications TEXT,
            min_experience_years REAL DEFAULT 0,
            education_requirement TEXT,
            publications_expected INTEGER NOT NULL DEFAULT 0,
            keywords TEXT,
            domain_tags TEXT,
            duration TEXT,
            stipend_or_funding TEXT,
            location TEXT,
            is_remote INTEGER NOT NULL DEFAULT 0,
            university TEXT DEFAULT 'IIT Madras',
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_research_opp_professor ON research_opportunities(professor_id);
        CREATE INDEX IF NOT EXISTS idx_research_opp_status ON research_opportunities(status);

        CREATE TABLE IF NOT EXISTS research_opportunity_matches (
            student_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            match_score REAL NOT NULL,
            semantic_score REAL NOT NULL,
            skills_score REAL NOT NULL,
            research_fit_score REAL NOT NULL,
            experience_score REAL NOT NULL,
            qualifications_score REAL NOT NULL,
            keywords_score REAL NOT NULL,
            confidence TEXT NOT NULL,
            matching_skills TEXT,
            missing_skills TEXT,
            reasons TEXT,
            profile_version REAL NOT NULL,
            opportunity_version REAL NOT NULL,
            computed_at REAL NOT NULL,
            PRIMARY KEY (student_id, opportunity_id)
        );

        CREATE INDEX IF NOT EXISTS idx_research_matches_student ON research_opportunity_matches(student_id, match_score DESC);
        CREATE INDEX IF NOT EXISTS idx_research_matches_opp ON research_opportunity_matches(opportunity_id, match_score DESC);

        CREATE TABLE IF NOT EXISTS research_opportunity_interests (
            interest_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            opportunity_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'interested',
            match_score_snapshot REAL,
            message TEXT,
            expressed_at REAL NOT NULL,
            UNIQUE(student_id, opportunity_id)
        );

        CREATE INDEX IF NOT EXISTS idx_research_interest_student ON research_opportunity_interests(student_id);
        CREATE INDEX IF NOT EXISTS idx_research_interest_opp ON research_opportunity_interests(opportunity_id);

        CREATE TABLE IF NOT EXISTS research_opportunity_invitations (
            invitation_id TEXT PRIMARY KEY,
            opportunity_id TEXT NOT NULL,
            opportunity_title TEXT,
            professor_id TEXT NOT NULL,
            professor_name TEXT,
            student_id TEXT NOT NULL,
            student_name TEXT,
            match_score REAL,
            score_breakdown_json TEXT,
            reasons_json TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'sent',
            created_at REAL NOT NULL,
            responded_at REAL
        );

        CREATE INDEX IF NOT EXISTS idx_research_invite_professor ON research_opportunity_invitations(professor_id);
        CREATE INDEX IF NOT EXISTS idx_research_invite_student ON research_opportunity_invitations(student_id);
    """)
    conn.commit()
    conn.close()


# ─── Research opportunities ─────────────────────────────────────────────────

OPPORTUNITY_LIST_FIELDS = [
    "research_areas", "required_skills", "preferred_skills",
    "required_qualifications", "preferred_qualifications", "keywords", "domain_tags",
]


def _row_to_opportunity(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for lf in OPPORTUNITY_LIST_FIELDS:
        d[lf] = json.loads(d[lf] or "[]")
    d["is_remote"] = bool(d["is_remote"])
    d["publications_expected"] = bool(d["publications_expected"])
    return d


def save_opportunity(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    opportunity_id = data.get("opportunity_id") or f"ROPP-{uuid.uuid4().hex[:10].upper()}"
    now = time.time()
    conn = _get_conn(db_path)
    fields = {
        "professor_id": data.get("professor_id", ""),
        "professor_name": data.get("professor_name", ""),
        "department": data.get("department", ""),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "opportunity_type": data.get("opportunity_type", "research_internship"),
        "degree_level": data.get("degree_level", ""),
        "research_areas": json.dumps(data.get("research_areas") or []),
        "required_skills": json.dumps(data.get("required_skills") or []),
        "preferred_skills": json.dumps(data.get("preferred_skills") or []),
        "required_qualifications": json.dumps(data.get("required_qualifications") or []),
        "preferred_qualifications": json.dumps(data.get("preferred_qualifications") or []),
        "min_experience_years": float(data.get("min_experience_years", 0) or 0),
        "education_requirement": data.get("education_requirement", ""),
        "publications_expected": 1 if data.get("publications_expected") else 0,
        "keywords": json.dumps(data.get("keywords") or []),
        "domain_tags": json.dumps(data.get("domain_tags") or []),
        "duration": data.get("duration", ""),
        "stipend_or_funding": data.get("stipend_or_funding", ""),
        "location": data.get("location", ""),
        "is_remote": 1 if data.get("is_remote") else 0,
        "university": data.get("university", "IIT Madras"),
        "status": data.get("status", "active"),
    }
    existing = conn.execute(
        "SELECT opportunity_id FROM research_opportunities WHERE opportunity_id = ?", (opportunity_id,),
    ).fetchone()
    if existing:
        set_clause = ", ".join(f"{c} = ?" for c in fields)
        conn.execute(
            f"UPDATE research_opportunities SET {set_clause}, updated_at = ? WHERE opportunity_id = ?",
            [*fields.values(), now, opportunity_id],
        )
    else:
        columns = ["opportunity_id", *fields.keys(), "created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO research_opportunities ({', '.join(columns)}) VALUES ({placeholders})",
            [opportunity_id, *fields.values(), now, now],
        )
    conn.commit()
    conn.close()
    return opportunity_id


def get_opportunity(opportunity_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM research_opportunities WHERE opportunity_id = ?", (opportunity_id,),
    ).fetchone()
    conn.close()
    return _row_to_opportunity(row) if row else None


def list_opportunities(
    status: Optional[str] = "active",
    opportunity_type: Optional[str] = None,
    degree_level: Optional[str] = None,
    professor_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    clauses = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if opportunity_type:
        clauses.append("opportunity_type = ?")
        params.append(opportunity_type)
    if degree_level:
        clauses.append("degree_level = ?")
        params.append(degree_level)
    if professor_id:
        clauses.append("professor_id = ?")
        params.append(professor_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM research_opportunities {where} ORDER BY created_at DESC", params,
    ).fetchall()
    conn.close()
    return [_row_to_opportunity(r) for r in rows]


def close_opportunity(opportunity_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE research_opportunities SET status = 'closed', updated_at = ? WHERE opportunity_id = ?",
        (time.time(), opportunity_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM research_opportunities WHERE opportunity_id = ?", (opportunity_id,),
    ).fetchone()
    conn.close()
    return _row_to_opportunity(row) if row else None


# ─── Match-score cache, read from both directions ──────────────────────────

def get_cached_opportunity_match(student_id: str, opportunity_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM research_opportunity_matches WHERE student_id = ? AND opportunity_id = ?",
        (student_id, opportunity_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for lf in ("matching_skills", "missing_skills", "reasons"):
        d[lf] = json.loads(d[lf] or "[]")
    return d


def save_opportunity_match(
    student_id: str, opportunity_id: str, score: Dict[str, Any],
    profile_version: float, opportunity_version: float,
    db_path: Optional[str] = None,
) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO research_opportunity_matches
           (student_id, opportunity_id, match_score, semantic_score, skills_score,
            research_fit_score, experience_score, qualifications_score, keywords_score,
            confidence, matching_skills, missing_skills, reasons,
            profile_version, opportunity_version, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(student_id, opportunity_id) DO UPDATE SET
             match_score=excluded.match_score, semantic_score=excluded.semantic_score,
             skills_score=excluded.skills_score, research_fit_score=excluded.research_fit_score,
             experience_score=excluded.experience_score, qualifications_score=excluded.qualifications_score,
             keywords_score=excluded.keywords_score, confidence=excluded.confidence,
             matching_skills=excluded.matching_skills, missing_skills=excluded.missing_skills,
             reasons=excluded.reasons, profile_version=excluded.profile_version,
             opportunity_version=excluded.opportunity_version, computed_at=excluded.computed_at""",
        (
            student_id, opportunity_id,
            score["match_score"], score["semantic_score"], score["skills_score"],
            score["research_fit_score"], score["experience_score"], score["qualifications_score"],
            score["keywords_score"], score["confidence"],
            json.dumps(score.get("matching_skills") or []),
            json.dumps(score.get("missing_skills") or []),
            json.dumps(score.get("reasons") or []),
            profile_version, opportunity_version, time.time(),
        ),
    )
    conn.commit()
    conn.close()


def _rows_to_matches(rows) -> List[Dict[str, Any]]:
    out = []
    for row in rows:
        d = dict(row)
        for lf in ("matching_skills", "missing_skills", "reasons"):
            d[lf] = json.loads(d[lf] or "[]")
        out.append(d)
    return out


def list_opportunity_matches_for_student(student_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM research_opportunity_matches WHERE student_id = ? ORDER BY match_score DESC",
        (student_id,),
    ).fetchall()
    conn.close()
    return _rows_to_matches(rows)


def list_opportunity_matches_for_opportunity(opportunity_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM research_opportunity_matches WHERE opportunity_id = ? ORDER BY match_score DESC",
        (opportunity_id,),
    ).fetchall()
    conn.close()
    return _rows_to_matches(rows)


# ─── Express interest (student-initiated) ──────────────────────────────────

def express_interest(
    student_id: str, opportunity_id: str, message: str = "",
    match_score_snapshot: Optional[float] = None, db_path: Optional[str] = None,
) -> Dict[str, Any]:
    conn = _get_conn(db_path)
    interest_id = f"RINT-{uuid.uuid4().hex[:10].upper()}"
    conn.execute(
        """INSERT OR IGNORE INTO research_opportunity_interests
           (interest_id, student_id, opportunity_id, status, match_score_snapshot, message, expressed_at)
           VALUES (?, ?, ?, 'interested', ?, ?, ?)""",
        (interest_id, student_id, opportunity_id, match_score_snapshot, message, time.time()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM research_opportunity_interests WHERE student_id = ? AND opportunity_id = ?",
        (student_id, opportunity_id),
    ).fetchone()
    conn.close()
    d = dict(row)
    d["already_interested"] = d["interest_id"] != interest_id
    return d


def get_interest(student_id: str, opportunity_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM research_opportunity_interests WHERE student_id = ? AND opportunity_id = ?",
        (student_id, opportunity_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_interests_for_student(student_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM research_opportunity_interests WHERE student_id = ? ORDER BY expressed_at DESC",
        (student_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ─── Invitations (professor-initiated, mirrors patent_offers) ─────────────

def create_invitation(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    invitation_id = f"RINV-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO research_opportunity_invitations
           (invitation_id, opportunity_id, opportunity_title, professor_id, professor_name,
            student_id, student_name, match_score, score_breakdown_json, reasons_json,
            message, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?)""",
        (
            invitation_id,
            data["opportunity_id"],
            data.get("opportunity_title", ""),
            data["professor_id"],
            data.get("professor_name", ""),
            data["student_id"],
            data.get("student_name", ""),
            data.get("match_score"),
            json.dumps(data.get("score_breakdown", {})),
            json.dumps(data.get("reasons", [])),
            data.get("message", ""),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return invitation_id


def _row_to_invitation(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "invitation_id": row["invitation_id"],
        "opportunity_id": row["opportunity_id"],
        "opportunity_title": row["opportunity_title"],
        "professor_id": row["professor_id"],
        "professor_name": row["professor_name"],
        "student_id": row["student_id"],
        "student_name": row["student_name"],
        "match_score": row["match_score"],
        "score_breakdown": json.loads(row["score_breakdown_json"] or "{}"),
        "reasons": json.loads(row["reasons_json"] or "[]"),
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
        "responded_at": row["responded_at"],
    }


def list_invitations_sent(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM research_opportunity_invitations WHERE professor_id = ? ORDER BY created_at DESC",
        (professor_id,),
    ).fetchall()
    conn.close()
    return [_row_to_invitation(r) for r in rows]


def list_invitations_received(student_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM research_opportunity_invitations WHERE student_id = ? ORDER BY created_at DESC",
        (student_id,),
    ).fetchall()
    conn.close()
    return [_row_to_invitation(r) for r in rows]


def respond_to_invitation(invitation_id: str, status: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE research_opportunity_invitations SET status = ?, responded_at = ? WHERE invitation_id = ?",
        (status, time.time(), invitation_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM research_opportunity_invitations WHERE invitation_id = ?", (invitation_id,),
    ).fetchone()
    conn.close()
    return _row_to_invitation(row) if row else None


__all__ = [
    "OPPORTUNITY_TYPES", "init_research_opportunity_tables",
    "save_opportunity", "get_opportunity", "list_opportunities", "close_opportunity",
    "get_cached_opportunity_match", "save_opportunity_match",
    "list_opportunity_matches_for_student", "list_opportunity_matches_for_opportunity",
    "express_interest", "get_interest", "list_interests_for_student",
    "create_invitation", "list_invitations_sent", "list_invitations_received", "respond_to_invitation",
]
