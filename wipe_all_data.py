"""
One-off destructive wipe of every Professor/Patent/Company/Problem-Statement/
Student/Employee/Institute/Job-Opportunity/Research-Opportunity row from the
live SQLite DB, plus every table whose rows become meaningless orphans once
those subjects are gone (interaction logs, match-score caches, etc.).

Run backup_before_wipe.py FIRST - this is not reversible on its own.

Two genuinely-orphaned legacy tables (no CREATE TABLE anywhere in current
source, superseded by match_interactions) are DROPPED outright rather than
just cleared, since nothing will recreate them.

users, weight_history (already empty, not one of the 9 named categories) and
sqlite_sequence (SQLite-internal bookkeeping) are left alone.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "collabv_data.db"

_TABLES_TO_CLEAR = [
    # Category tables (the 9 named entity types)
    "student_profiles", "employee_profiles", "institute_profiles", "company_profiles",
    "professor_profiles", "patent_listings", "problem_statements", "job_postings",
    "research_opportunities",
    # Directly-dependent tables (orphaned once their subjects are gone)
    "company_requests", "patent_smart_matches", "job_applications", "job_match_scores",
    "research_opportunity_matches", "research_opportunity_interests",
    "research_opportunity_invitations", "buyer_profiles", "marketplace_proposals",
    "marketplace_inquiries", "marketplace_events", "marketplace_explanations",
    "professor_claims", "patent_transactions", "patent_offers", "listing_inquiries",
    "wishlist_items", "negotiation_messages", "technology_requests",
    "professor_match_interactions", "match_interactions", "match_results", "feedback",
    "deal_assessments", "match_explanations",
]

_TABLES_TO_DROP = [
    "patent_discovery_interactions",
    "patent_match_interactions",
]


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    print("Before:")
    before_counts = {}
    for table in _TABLES_TO_CLEAR:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            count = None
        before_counts[table] = count
        print(f"  {table}: {count}")

    conn.execute("BEGIN")
    try:
        for table in _TABLES_TO_CLEAR:
            if before_counts[table] is not None:
                conn.execute(f"DELETE FROM {table}")
        for table in _TABLES_TO_DROP:
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    conn.execute("VACUUM")

    print("\nAfter:")
    for table in _TABLES_TO_CLEAR:
        if before_counts[table] is not None:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table}: {count}")

    for table in _TABLES_TO_DROP:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,),
        ).fetchone()
        print(f"  {table}: {'still exists!' if exists else 'dropped'}")

    conn.close()
    print("\nWipe complete.")


if __name__ == "__main__":
    main()
