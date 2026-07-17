"""
CollabV AI - Job Postings + Resume Matching persistence
========================================================
Backs AI Matching Engine 9: company job postings, a student's
applications to them, and a persisted per-(student, job) match-score cache.

Mirrors patent_marketplace_db.py's style: sync sqlite3, snake_case tables,
JSON blobs in TEXT columns, idempotent CREATE TABLE IF NOT EXISTS.

job_match_scores is a materialized, queryable cache (not an opaque blob like
marketplace_explanations) because the Student Dashboard's "AI Matching Engine 9"
tab needs to sort/filter a whole list of scored jobs per request. Staleness
(and therefore auto-refresh) is detected by comparing the stored
profile_version/job_version against the live student_profiles.updated_at /
job_postings.updated_at at read time - see api.py's job-matches endpoint,
which recomputes and upserts any stale/missing rows before returning.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_job_matching_tables(db_path: Optional[str] = None) -> None:
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS job_postings (
            job_id TEXT PRIMARY KEY,
            company_id TEXT,
            company_name TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            required_skills TEXT,
            preferred_skills TEXT,
            min_experience_years REAL DEFAULT 0,
            education_requirement TEXT,
            certifications_preferred TEXT,
            keywords TEXT,
            domain_tags TEXT,
            employment_type TEXT NOT NULL DEFAULT 'full_time',
            is_remote INTEGER NOT NULL DEFAULT 0,
            location TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_job_postings_status ON job_postings(status);
        CREATE INDEX IF NOT EXISTS idx_job_postings_created ON job_postings(created_at);

        CREATE TABLE IF NOT EXISTS job_applications (
            application_id TEXT PRIMARY KEY,
            student_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied',
            match_score_snapshot REAL,
            applied_at REAL NOT NULL,
            UNIQUE(student_id, job_id)
        );

        CREATE INDEX IF NOT EXISTS idx_job_apps_student ON job_applications(student_id);
        CREATE INDEX IF NOT EXISTS idx_job_apps_job ON job_applications(job_id);

        CREATE TABLE IF NOT EXISTS job_match_scores (
            student_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            match_score REAL NOT NULL,
            semantic_score REAL NOT NULL,
            skills_score REAL NOT NULL,
            experience_score REAL NOT NULL,
            education_score REAL NOT NULL,
            certifications_score REAL NOT NULL,
            keywords_score REAL NOT NULL,
            confidence TEXT NOT NULL,
            matching_skills TEXT,
            missing_skills TEXT,
            reasons TEXT,
            profile_version REAL NOT NULL,
            job_version REAL NOT NULL,
            computed_at REAL NOT NULL,
            PRIMARY KEY (student_id, job_id)
        );

        CREATE INDEX IF NOT EXISTS idx_job_match_scores_student ON job_match_scores(student_id, match_score DESC);
    """)
    conn.commit()
    conn.close()


# ─── Job postings ───────────────────────────────────────────────────────────

JOB_LIST_FIELDS = [
    "required_skills", "preferred_skills", "certifications_preferred",
    "keywords", "domain_tags",
]


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    for lf in JOB_LIST_FIELDS:
        d[lf] = json.loads(d[lf] or "[]")
    d["is_remote"] = bool(d["is_remote"])
    return d


def save_job_posting(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    job_id = data.get("job_id") or f"JOB-{uuid.uuid4().hex[:10].upper()}"
    now = time.time()
    conn = _get_conn(db_path)
    fields = {
        "company_id": data.get("company_id", ""),
        "company_name": data.get("company_name", ""),
        "title": data.get("title", ""),
        "description": data.get("description", ""),
        "required_skills": json.dumps(data.get("required_skills") or []),
        "preferred_skills": json.dumps(data.get("preferred_skills") or []),
        "min_experience_years": float(data.get("min_experience_years", 0) or 0),
        "education_requirement": data.get("education_requirement", ""),
        "certifications_preferred": json.dumps(data.get("certifications_preferred") or []),
        "keywords": json.dumps(data.get("keywords") or []),
        "domain_tags": json.dumps(data.get("domain_tags") or []),
        "employment_type": data.get("employment_type", "full_time"),
        "is_remote": 1 if data.get("is_remote") else 0,
        "location": data.get("location", ""),
        "status": data.get("status", "active"),
    }
    existing = conn.execute("SELECT job_id FROM job_postings WHERE job_id = ?", (job_id,)).fetchone()
    if existing:
        set_clause = ", ".join(f"{c} = ?" for c in fields)
        conn.execute(
            f"UPDATE job_postings SET {set_clause}, updated_at = ? WHERE job_id = ?",
            [*fields.values(), now, job_id],
        )
    else:
        columns = ["job_id", *fields.keys(), "created_at", "updated_at"]
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO job_postings ({', '.join(columns)}) VALUES ({placeholders})",
            [job_id, *fields.values(), now, now],
        )
    conn.commit()
    conn.close()
    return job_id


def get_job_posting(job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute("SELECT * FROM job_postings WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return _row_to_job(row) if row else None


def list_job_postings(
    status: Optional[str] = "active",
    employment_type: Optional[str] = None,
    is_remote: Optional[bool] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    clauses = []
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if employment_type:
        clauses.append("employment_type = ?")
        params.append(employment_type)
    if is_remote is not None:
        clauses.append("is_remote = ?")
        params.append(1 if is_remote else 0)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM job_postings {where} ORDER BY created_at DESC", params,
    ).fetchall()
    conn.close()
    return [_row_to_job(r) for r in rows]


def close_job_posting(job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    conn.execute(
        "UPDATE job_postings SET status = 'closed', updated_at = ? WHERE job_id = ?",
        (time.time(), job_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM job_postings WHERE job_id = ?", (job_id,)).fetchone()
    conn.close()
    return _row_to_job(row) if row else None


# ─── Match-score cache (auto-refreshed by api.py against profile/job updated_at) ──

def get_cached_match(student_id: str, job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM job_match_scores WHERE student_id = ? AND job_id = ?",
        (student_id, job_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    for lf in ("matching_skills", "missing_skills", "reasons"):
        d[lf] = json.loads(d[lf] or "[]")
    return d


def save_match_score(
    student_id: str, job_id: str, score: Dict[str, Any],
    profile_version: float, job_version: float,
    db_path: Optional[str] = None,
) -> None:
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO job_match_scores
           (student_id, job_id, match_score, semantic_score, skills_score,
            experience_score, education_score, certifications_score, keywords_score,
            confidence, matching_skills, missing_skills, reasons,
            profile_version, job_version, computed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(student_id, job_id) DO UPDATE SET
             match_score=excluded.match_score, semantic_score=excluded.semantic_score,
             skills_score=excluded.skills_score, experience_score=excluded.experience_score,
             education_score=excluded.education_score, certifications_score=excluded.certifications_score,
             keywords_score=excluded.keywords_score, confidence=excluded.confidence,
             matching_skills=excluded.matching_skills, missing_skills=excluded.missing_skills,
             reasons=excluded.reasons, profile_version=excluded.profile_version,
             job_version=excluded.job_version, computed_at=excluded.computed_at""",
        (
            student_id, job_id,
            score["match_score"], score["semantic_score"], score["skills_score"],
            score["experience_score"], score["education_score"], score["certifications_score"],
            score["keywords_score"], score["confidence"],
            json.dumps(score.get("matching_skills") or []),
            json.dumps(score.get("missing_skills") or []),
            json.dumps(score.get("reasons") or []),
            profile_version, job_version, time.time(),
        ),
    )
    conn.commit()
    conn.close()


def list_match_scores_for_student(student_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM job_match_scores WHERE student_id = ? ORDER BY match_score DESC",
        (student_id,),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        d = dict(row)
        for lf in ("matching_skills", "missing_skills", "reasons"):
            d[lf] = json.loads(d[lf] or "[]")
        out.append(d)
    return out


# ─── Applications ───────────────────────────────────────────────────────────

def create_application(
    student_id: str, job_id: str, match_score_snapshot: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Idempotent: re-applying to the same job returns the existing row
    (via the UNIQUE(student_id, job_id) constraint) rather than erroring or
    duplicating - mirrors patent_marketplace_db.add_wishlist_item's
    INSERT OR IGNORE idiom."""
    conn = _get_conn(db_path)
    application_id = f"APP-{uuid.uuid4().hex[:10].upper()}"
    conn.execute(
        """INSERT OR IGNORE INTO job_applications
           (application_id, student_id, job_id, status, match_score_snapshot, applied_at)
           VALUES (?, ?, ?, 'applied', ?, ?)""",
        (application_id, student_id, job_id, match_score_snapshot, time.time()),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM job_applications WHERE student_id = ? AND job_id = ?",
        (student_id, job_id),
    ).fetchone()
    conn.close()
    d = dict(row)
    d["already_applied"] = d["application_id"] != application_id
    return d


def get_application(student_id: str, job_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM job_applications WHERE student_id = ? AND job_id = ?",
        (student_id, job_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def list_applications_for_student(student_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute(
        "SELECT * FROM job_applications WHERE student_id = ? ORDER BY applied_at DESC",
        (student_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


__all__ = [
    "init_job_matching_tables",
    "save_job_posting", "get_job_posting", "list_job_postings", "close_job_posting",
    "get_cached_match", "save_match_score", "list_match_scores_for_student",
    "create_application", "get_application", "list_applications_for_student",
]
