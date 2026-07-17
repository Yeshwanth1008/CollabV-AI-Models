"""
CollabV AI - SQLite Persistence Layer
=======================================
Stores company requests, match results, and feedback.
Uses Python built-in sqlite3 — no extra dependencies.
"""

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional


DB_PATH = str(Path(__file__).parent.parent / "collabv_data.db")


def _get_conn(db_path: str = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = None):
    """Create tables if they don't exist."""
    conn = _get_conn(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS company_requests (
            company_id TEXT PRIMARY KEY,
            company_name TEXT NOT NULL,
            industry TEXT,
            technical_area TEXT,
            required_expertise TEXT,
            technology_stack TEXT,
            project_description TEXT,
            challenges TEXT,
            collaboration_type TEXT,
            location_preference TEXT,
            research_level TEXT,
            budget_tier TEXT,
            timeline_months INTEGER,
            raw_text TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_results (
            match_id TEXT PRIMARY KEY,
            company_id TEXT NOT NULL,
            company_name TEXT,
            top_score REAL,
            results_json TEXT NOT NULL,
            parsed_tags_json TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (company_id) REFERENCES company_requests(company_id)
        );

        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            professor_id TEXT NOT NULL,
            action TEXT NOT NULL,
            reason TEXT,
            created_at REAL NOT NULL,
            FOREIGN KEY (match_id) REFERENCES match_results(match_id)
        );

        CREATE TABLE IF NOT EXISTS deal_assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            professor_id TEXT NOT NULL,
            success_probability REAL,
            confidence_level TEXT,
            band TEXT,
            assessment_json TEXT,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS match_explanations (
            cache_key TEXT PRIMARY KEY,
            professor_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            explanation_json TEXT NOT NULL,
            created_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS weight_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weights_json TEXT NOT NULL,
            improvement_score REAL,
            feedback_count INTEGER,
            applied_at REAL NOT NULL,
            note TEXT
        );
    """)
    conn.commit()
    conn.close()


def save_deal_assessment(match_id: str, assessment: dict, db_path: str = None):
    """Persist a per-professor deal assessment."""
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO deal_assessments
           (match_id, professor_id, success_probability, confidence_level, band,
            assessment_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            match_id,
            assessment.get("professor_id", ""),
            assessment.get("success_probability", 0),
            assessment.get("confidence_level", ""),
            assessment.get("band", ""),
            json.dumps(assessment),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def save_request(company_id: str, data: dict, db_path: str = None):
    """Save a company request to the database."""
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO company_requests
           (company_id, company_name, industry, technical_area, required_expertise,
            technology_stack, project_description, challenges, collaboration_type,
            location_preference, research_level, budget_tier, timeline_months,
            raw_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            company_id,
            data.get("company_name", ""),
            data.get("industry", ""),
            json.dumps(data.get("technical_area", [])),
            json.dumps(data.get("required_expertise", [])),
            json.dumps(data.get("technology_stack", data.get("tech_stack", []))),
            data.get("project_description", ""),
            data.get("challenges", ""),
            data.get("collaboration_type", ""),
            data.get("location_preference", ""),
            data.get("research_level", ""),
            data.get("budget_tier", ""),
            data.get("timeline_months", 0),
            data.get("raw_text", ""),
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def save_result(match_id: str, company_id: str, company_name: str,
                results: list, parsed_tags: dict = None, db_path: str = None):
    """Save match results to the database."""
    top_score = results[0]["score"] if results else 0
    # When the embedding engine is loaded, match scores arrive as numpy
    # float32/float64. json.dumps can't serialize those out of the box; the
    # default= hook converts any numpy scalar to its Python equivalent.
    def _np_default(o):
        try:
            import numpy as _np
            if isinstance(o, _np.generic):
                return o.item()
        except ImportError:
            pass
        raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT OR REPLACE INTO match_results
           (match_id, company_id, company_name, top_score, results_json,
            parsed_tags_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            match_id,
            company_id,
            company_name,
            float(top_score) if top_score is not None else 0,
            json.dumps(results, default=_np_default),
            json.dumps(parsed_tags, default=_np_default) if parsed_tags else None,
            time.time(),
        ),
    )
    conn.commit()
    conn.close()


def save_feedback(match_id: str, professor_id: str, action: str,
                  reason: str = "", db_path: str = None):
    """Save feedback to the database."""
    conn = _get_conn(db_path)
    conn.execute(
        """INSERT INTO feedback (match_id, professor_id, action, reason, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (match_id, professor_id, action, reason, time.time()),
    )
    conn.commit()
    conn.close()


def get_history(limit: int = 20, db_path: str = None) -> List[Dict]:
    """Get recent match history."""
    conn = _get_conn(db_path)
    rows = conn.execute(
        """SELECT m.match_id, m.company_id, m.company_name, m.top_score,
                  m.results_json, m.parsed_tags_json, m.created_at
           FROM match_results m
           ORDER BY m.created_at DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()

    history = []
    for row in rows:
        results = json.loads(row["results_json"])
        parsed_tags = json.loads(row["parsed_tags_json"]) if row["parsed_tags_json"] else None
        history.append({
            "match_id": row["match_id"],
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "top_score": row["top_score"],
            "num_results": len(results),
            "top_professor": results[0]["professor_name"] if results else None,
            "top_department": results[0]["department"] if results else None,
            "parsed_tags": parsed_tags,
            "created_at": row["created_at"],
        })
    return history


def get_stats(db_path: str = None) -> Dict:
    """Get overall statistics."""
    conn = _get_conn(db_path)
    total_requests = conn.execute("SELECT COUNT(*) FROM company_requests").fetchone()[0]
    total_matches = conn.execute("SELECT COUNT(*) FROM match_results").fetchone()[0]
    total_feedback = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    avg_score = conn.execute("SELECT AVG(top_score) FROM match_results").fetchone()[0]
    conn.close()

    return {
        "total_requests": total_requests,
        "total_matches": total_matches,
        "total_feedback": total_feedback,
        "avg_top_score": round(avg_score, 1) if avg_score else 0,
    }
