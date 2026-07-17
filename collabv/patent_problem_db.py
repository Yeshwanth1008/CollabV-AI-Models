"""
CollabV AI - Patent <-> Problem Statement persistence
=========================================================
Mirrors collabv/database.py style: sync sqlite3, snake_case tables, JSON
blobs in TEXT columns.

Backs Matching Engine 3 (patent -> problem statement) and Matching Engine 4
(problem statement -> patent). Two tables:

  problem_statements    - the 50-item compendium, seeded once from
                          data/problem_statements.json.
  patent_smart_matches  - persisted match results for both directions, with
                          a dashboard_visibility flag so the Professor
                          Dashboard and Company Dashboard can read a stable,
                          already-ranked list instead of recomputing on
                          every page load.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")
PROBLEM_STATEMENTS_SEED_FILE = str(Path(__file__).parent / "data" / "problem_statements.json")

DIRECTION_PATENT_TO_PROBLEM = "patent_to_problem"
DIRECTION_PROBLEM_TO_PATENT = "problem_to_patent"


def _get_conn(db_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_patent_problem_tables(db_path: Optional[str] = None) -> None:
    """Create tables if they don't exist, then seed problem statements."""
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS problem_statements (
            id TEXT PRIMARY KEY,
            sector TEXT,
            title TEXT NOT NULL,
            description TEXT,
            problem_statement TEXT,
            expected_outcomes TEXT,
            company_id TEXT,
            created_at REAL
        );

        CREATE TABLE IF NOT EXISTS patent_smart_matches (
            match_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            source_id TEXT NOT NULL,
            patent_id TEXT,
            patent_number TEXT,
            patent_title TEXT,
            professor_id TEXT,
            professor_name TEXT,
            department TEXT,
            problem_statement_id TEXT NOT NULL,
            match_score REAL NOT NULL,
            score_breakdown_json TEXT,
            reasons_json TEXT,
            dashboard_visibility INTEGER NOT NULL DEFAULT 1,
            model_version TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_psm_direction_source
            ON patent_smart_matches(direction, source_id);
        CREATE INDEX IF NOT EXISTS idx_psm_professor
            ON patent_smart_matches(professor_id);
        CREATE INDEX IF NOT EXISTS idx_psm_problem_statement
            ON patent_smart_matches(problem_statement_id);
    """)
    # Idempotent ALTERs for DBs that pre-date these columns. Additive only.
    for sql in (
        "ALTER TABLE problem_statements ADD COLUMN company_id TEXT",
        "ALTER TABLE problem_statements ADD COLUMN created_at REAL",
    ):
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
    conn.close()
    # Live-data-only: problem statements are no longer auto-seeded from
    # data/problem_statements.json on startup - that file stays on disk as an
    # untouched archive. The only way a problem statement enters this table
    # now is a live POST /problem-statements submission (see save_problem_statement).


def _row_to_problem_statement(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["expected_outcomes"] = json.loads(d["expected_outcomes"] or "[]")
    return d


def save_problem_statement(data: Dict[str, Any], db_path: Optional[str] = None) -> str:
    """Live submission path - a registered company posting a real R&D need,
    replacing the retired static 50-item compendium as the only way a
    problem statement enters this table (see init_patent_problem_tables)."""
    problem_id = f"PS-{uuid.uuid4().hex[:10].upper()}"
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO problem_statements
           (id, sector, title, description, problem_statement, expected_outcomes, company_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            problem_id,
            data.get("sector", ""),
            data["title"],
            data.get("description", ""),
            data.get("problem_statement", ""),
            json.dumps(data.get("expected_outcomes") or []),
            data.get("company_id", ""),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return problem_id


def get_problem_statements(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    conn = _get_conn(db_path)
    rows = conn.execute("SELECT * FROM problem_statements ORDER BY id").fetchall()
    conn.close()
    return [_row_to_problem_statement(r) for r in rows]


def get_problem_statement(problem_id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = _get_conn(db_path)
    row = conn.execute(
        "SELECT * FROM problem_statements WHERE id = ?", (problem_id,)
    ).fetchone()
    conn.close()
    return _row_to_problem_statement(row) if row else None


def save_smart_matches(
    direction: str,
    source_id: str,
    matches: List[Dict[str, Any]],
    model_version: str,
    db_path: Optional[str] = None,
) -> int:
    """Replace all previously persisted matches for this (direction, source_id)
    with a fresh set, so the dashboard always reflects the latest run.

    Each item in `matches` is expected to have keys: patent_id, patent_number,
    patent_title, professor_id, professor_name, department,
    problem_statement_id, match_score, score_breakdown, reasons.
    """
    conn = _get_conn(db_path)
    conn.execute(
        "DELETE FROM patent_smart_matches WHERE direction = ? AND source_id = ?",
        (direction, source_id),
    )
    now = time.time()
    conn.executemany(
        """INSERT INTO patent_smart_matches
           (match_id, direction, source_id, patent_id, patent_number, patent_title,
            professor_id, professor_name, department, problem_statement_id,
            match_score, score_breakdown_json, reasons_json,
            dashboard_visibility, model_version, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
        [
            (
                str(uuid.uuid4()),
                direction,
                source_id,
                m.get("patent_id", ""),
                m.get("patent_number", ""),
                m.get("patent_title", ""),
                m.get("professor_id", ""),
                m.get("professor_name", ""),
                m.get("department", ""),
                m["problem_statement_id"],
                float(m["match_score"]),
                json.dumps(m.get("score_breakdown", {})),
                json.dumps(m.get("reasons", [])),
                model_version,
                now,
            )
            for m in matches
        ],
    )
    conn.commit()
    conn.close()
    return len(matches)


def _row_to_match(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "match_id": row["match_id"],
        "direction": row["direction"],
        "patent_id": row["patent_id"],
        "patent_number": row["patent_number"],
        "patent_title": row["patent_title"],
        "professor_id": row["professor_id"],
        "professor_name": row["professor_name"],
        "department": row["department"],
        "problem_statement_id": row["problem_statement_id"],
        "match_score": row["match_score"],
        "score_breakdown": json.loads(row["score_breakdown_json"] or "{}"),
        "reasons": json.loads(row["reasons_json"] or "[]"),
        "dashboard_visibility": bool(row["dashboard_visibility"]),
        "model_version": row["model_version"],
        "created_at": row["created_at"],
    }


def get_matches_for_professor(professor_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Professor Dashboard: patent-to-problem matches for this professor's patents,
    joined with the matched problem statement's title/sector."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT m.*, p.title AS ps_title, p.sector AS ps_sector
           FROM patent_smart_matches m
           JOIN problem_statements p ON p.id = m.problem_statement_id
           WHERE m.direction = ? AND m.source_id = ? AND m.dashboard_visibility = 1
           ORDER BY m.match_score DESC""",
        (DIRECTION_PATENT_TO_PROBLEM, professor_id),
    ).fetchall()
    conn.close()
    out = []
    for row in rows:
        item = _row_to_match(row)
        item["problem_title"] = row["ps_title"]
        item["problem_sector"] = row["ps_sector"]
        out.append(item)
    return out


def get_matches_for_problem_statement(problem_statement_id: str, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Company Dashboard: problem-to-patent matches for a given problem statement."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT * FROM patent_smart_matches
           WHERE direction = ? AND source_id = ? AND dashboard_visibility = 1
           ORDER BY match_score DESC""",
        (DIRECTION_PROBLEM_TO_PATENT, problem_statement_id),
    ).fetchall()
    conn.close()
    return [_row_to_match(r) for r in rows]


__all__ = [
    "init_patent_problem_tables",
    "get_problem_statements",
    "get_problem_statement",
    "save_problem_statement",
    "save_smart_matches",
    "get_matches_for_professor",
    "get_matches_for_problem_statement",
    "DIRECTION_PATENT_TO_PROBLEM",
    "DIRECTION_PROBLEM_TO_PATENT",
]
