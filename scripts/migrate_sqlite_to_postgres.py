"""
Migrate CollabV AI SQLite data to PostgreSQL + pgvector.

Reads all rows from collabv_data.db (SQLite), parses any JSON-text columns
back into structured JSONB, copies professors into the new
`professor_profiles` table along with their FAISS embeddings.

Usage:
    export DATABASE_URL=postgresql://collabv:pw@localhost:5432/collabv
    python scripts/migrate_sqlite_to_postgres.py \
        --sqlite collabv_data.db \
        --professors iitm_professors_with_patents.json \
        [--faiss-index collabv_embeddings.index]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from collabv import db_postgres as pg
from collabv.embeddings import EmbeddingEngine


# ─── SQLite read helpers ──────────────────────────────────────────────────

def _open_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _maybe_json(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


# ─── Migration steps ──────────────────────────────────────────────────────

async def migrate_company_requests(sqlite: sqlite3.Connection) -> int:
    rows = sqlite.execute("SELECT * FROM company_requests").fetchall()
    print(f"  Migrating {len(rows)} company_requests...")
    async with pg.get_session() as s:
        for r in rows:
            s.add(pg.CompanyRequestRow(
                company_id=r["company_id"], company_name=r["company_name"],
                industry=r["industry"],
                technical_area=_maybe_json(r["technical_area"]),
                required_expertise=_maybe_json(r["required_expertise"]),
                technology_stack=_maybe_json(r["technology_stack"]),
                project_description=r["project_description"],
                challenges=r["challenges"],
                collaboration_type=r["collaboration_type"],
                location_preference=r["location_preference"],
                research_level=r["research_level"],
                budget_tier=r["budget_tier"],
                timeline_months=r["timeline_months"],
                raw_text=r["raw_text"],
                created_at=r["created_at"],
            ))
        await s.commit()
    return len(rows)


async def migrate_match_results(sqlite: sqlite3.Connection) -> int:
    rows = sqlite.execute("SELECT * FROM match_results").fetchall()
    print(f"  Migrating {len(rows)} match_results...")
    async with pg.get_session() as s:
        for r in rows:
            s.add(pg.MatchResultRow(
                match_id=r["match_id"], company_id=r["company_id"],
                company_name=r["company_name"], top_score=r["top_score"],
                results=_maybe_json(r["results_json"]),
                parsed_tags=_maybe_json(r["parsed_tags_json"]),
                created_at=r["created_at"],
            ))
        await s.commit()
    return len(rows)


async def migrate_feedback(sqlite: sqlite3.Connection) -> int:
    rows = sqlite.execute("SELECT * FROM feedback").fetchall()
    print(f"  Migrating {len(rows)} feedback rows...")
    async with pg.get_session() as s:
        for r in rows:
            s.add(pg.FeedbackRow(
                match_id=r["match_id"], professor_id=r["professor_id"],
                action=r["action"], reason=r["reason"], created_at=r["created_at"],
            ))
        await s.commit()
    return len(rows)


async def migrate_explanations(sqlite: sqlite3.Connection) -> int:
    try:
        rows = sqlite.execute("SELECT * FROM match_explanations").fetchall()
    except sqlite3.OperationalError:
        return 0
    print(f"  Migrating {len(rows)} match_explanations...")
    async with pg.get_session() as s:
        for r in rows:
            s.add(pg.MatchExplanationRow(
                cache_key=r["cache_key"], professor_id=r["professor_id"],
                request_hash=r["request_hash"],
                explanation=_maybe_json(r["explanation_json"]),
                created_at=r["created_at"],
            ))
        await s.commit()
    return len(rows)


async def migrate_weight_history(sqlite: sqlite3.Connection) -> int:
    try:
        rows = sqlite.execute("SELECT * FROM weight_history").fetchall()
    except sqlite3.OperationalError:
        return 0
    print(f"  Migrating {len(rows)} weight_history rows...")
    async with pg.get_session() as s:
        for r in rows:
            s.add(pg.WeightHistoryRow(
                weights=_maybe_json(r["weights_json"]),
                improvement_score=r["improvement_score"],
                feedback_count=r["feedback_count"],
                applied_at=r["applied_at"],
                note=r["note"],
            ))
        await s.commit()
    return len(rows)


async def migrate_professors(prof_path: str, faiss_path: str | None) -> int:
    with open(prof_path, encoding="utf-8") as f:
        professors = json.load(f)
    print(f"  Migrating {len(professors)} professors...")

    # Embeddings - rebuild from FAISS index if provided, else regenerate
    id_to_emb = {}
    ee = EmbeddingEngine()
    if not ee.is_ready:
        print("    sentence-transformers not available - skipping embeddings")
    else:
        if faiss_path and Path(faiss_path).exists():
            ee.load_index(faiss_path)
            print(f"    Loaded {len(ee.prof_ids)} embeddings from {faiss_path}")
            # Map prof_id -> embedding (faiss has just the matrix)
            if ee._matrix is not None:
                for i, pid in enumerate(ee.prof_ids):
                    id_to_emb[pid] = ee._matrix[i].tolist()
            elif ee.use_faiss and ee.index is not None:
                import numpy as np
                vecs = ee.index.reconstruct_n(0, ee.index.ntotal)
                for i, pid in enumerate(ee.prof_ids):
                    id_to_emb[pid] = vecs[i].tolist()
        else:
            print("    Re-encoding professor embeddings on the fly...")
            texts = [ee._professor_text(p) for p in professors]
            embs = ee.encode_batch(texts, show_progress=False)
            for p, v in zip(professors, embs):
                id_to_emb[str(p.get("professor_id"))] = v.tolist()

    async with pg.get_session() as s:
        for p in professors:
            pid = str(p.get("professor_id"))
            s.add(pg.ProfessorProfile(
                professor_id=pid,
                name=p.get("name", ""),
                department=p.get("department", ""),
                biography=p.get("biography", ""),
                research_areas=p.get("research_areas", []),
                publications=p.get("publications", []),
                patents=p.get("patents", []),
                raw_profile=p,
                embedding=id_to_emb.get(pid),
                updated_at=time.time(),
            ))
        await s.commit()
    return len(professors)


# ─── Main ─────────────────────────────────────────────────────────────────

async def main_async(args) -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set. Example:")
        print("   export DATABASE_URL=postgresql://collabv:pw@localhost:5432/collabv")
        sys.exit(2)

    print(f"Connecting to: {os.environ['DATABASE_URL']}")
    await pg.init_db_async()
    print("  Schema created.\n")

    sqlite = _open_sqlite(args.sqlite)
    counts: dict[str, int] = {}
    print(f"Reading from SQLite: {args.sqlite}")
    counts["company_requests"] = await migrate_company_requests(sqlite)
    counts["match_results"] = await migrate_match_results(sqlite)
    counts["feedback"] = await migrate_feedback(sqlite)
    counts["match_explanations"] = await migrate_explanations(sqlite)
    counts["weight_history"] = await migrate_weight_history(sqlite)
    sqlite.close()

    if Path(args.professors).exists():
        counts["professor_profiles"] = await migrate_professors(args.professors, args.faiss_index)
    else:
        print(f"WARN: {args.professors} not found - skipping professor migration")

    print("\nMigration complete:")
    for k, v in counts.items():
        print(f"  {k:25s} {v}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="collabv_data.db")
    parser.add_argument("--professors", default="iitm_professors_with_patents.json")
    parser.add_argument("--faiss-index", default="collabv_embeddings.index")
    args = parser.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
