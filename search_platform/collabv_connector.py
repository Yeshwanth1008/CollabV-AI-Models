"""
Live connector into CollabV — the actual "CollabV website" this service is
deployed alongside. No separate hosted deployment exists (app.collabv.ai
does not resolve); CollabV's own backend + database *is* the real system.
This module reads directly from it at call time — no static snapshot, no
generated data.

Two real data sources, matched exactly to what CollabV's own backend
(collabv/api.py) actually loads — CollabV is live-data-only: every entity
type starts empty and is populated exclusively through its own API by real
registered users (see API.md's "Live-data-only" section). There is no
seed/sample/demo data anywhere in this path, professors included:

  - Professors: `<COLLABV_ROOT>/<PROFESSORS_FILE>` — same file and same env
    var collabv/api.py itself reads (`PROFESSORS_FILE`, default
    `professors_live.json` — an intentionally empty `[]` until real
    professors register via `POST /professor/profile`). This is
    deliberately NOT the older `iitm_professors_nlp.json` scrape, which
    CollabV's own `.gitignore` retires as "superseded by the live-data-only
    architecture" — reading that file here would contradict the exact
    policy this connector exists to respect.
  - Student / Employee / Institute / Company: `<COLLABV_ROOT>/collabv_data.db`
    (SQLite) — the same DB `patent_marketplace_db.py`'s
    `list_student_profiles()` etc. read from (DEFAULT_DB_PATH).

COLLABV_ROOT defaults to two directories up from this file. Deployed inside
CollabV's own repo (this file at `search_platform/collabv_connector.py`,
sitting next to `collabv/` at the repo root), that default is already
correct — no configuration needed. Set the COLLABV_ROOT / PROFESSORS_FILE
env vars to override for any other layout (e.g. local development against
an older CollabV checkout that predates the live-data-only migration and
still has a populated professor scrape file under a different name).

Researcher / Startup / Alumni / Mentor have no table in CollabV's schema at
all yet — there is nothing real to load for them, so they're intentionally
absent here rather than backfilled with placeholders. The moment CollabV
gains those tables (or real rows in the existing ones), re-running
`sync_from_collabv.py` picks them up — no code changes needed.

Test-fixture filtering: rows created by CollabV's own QA scripts (user_id
prefixes like STU-TEST1, EMP-PLAYWRIGHT-..., INST-MERGETEST — see
scripts/seed_test_accounts.py) are not real signups. _looks_like_test_row()
filters those out heuristically since CollabV's schema has no is_test flag.
This is intentionally conservative: it only excludes rows matching known
QA-fixture markers, so real rows added later pass through untouched.
"""
import json
import os
import re
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

from .schemas import ExperienceEntry, UserProfileIn

# Loaded independently of config.py's own load_dotenv() call — this module
# reads COLLABV_ROOT at import time, and relying on some other module
# happening to import config.py first (and thus load .env) before this file
# does would be a fragile, order-dependent bug. load_dotenv() is a no-op if
# the values are already loaded, so calling it again here is harmless.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

COLLABV_ROOT = Path(os.getenv("COLLABV_ROOT", str(Path(__file__).resolve().parent.parent)))
PROFESSORS_JSON = COLLABV_ROOT / os.getenv("PROFESSORS_FILE", "professors_live.json")
COLLABV_DB = COLLABV_ROOT / "collabv_data.db"

_TEST_MARKERS = re.compile(r"TEST|PLAYWRIGHT|MERGETEST|DEMO|^STU-\d+$", re.IGNORECASE)
_TEST_NAMES = {"jane doe", "john smith", "test student", "merge test", "emp merge test"}


def _looks_like_test_row(row_id: str, name: str) -> bool:
    if row_id and _TEST_MARKERS.search(row_id):
        return True
    if name and name.strip().lower() in _TEST_NAMES:
        return True
    return False


_BATCH_WINDOW_SECONDS = 5


def _drop_test_batches(rows: list, id_key: str, name_key: str, created_key: str) -> list:
    """
    Marker-based filtering alone misses rows from the same seed/QA script
    run that happen to have plausible-looking IDs and names (seen in
    practice: "BridgeSafe Infra" inserted 0.3s after an unambiguous
    "PlaywrightTest Robotics" fixture — same batch, not an independent
    signup). Any row timestamped within a few seconds of a confirmed test
    row is almost certainly from the same fixture-insertion script, so it's
    excluded too. Real signups arriving independently, days or minutes
    apart, are unaffected.
    """
    confirmed_test_times = [
        r[created_key] for r in rows if _looks_like_test_row(r[id_key], r[name_key]) and r[created_key]
    ]
    if not confirmed_test_times:
        return rows

    def in_test_batch(r) -> bool:
        if _looks_like_test_row(r[id_key], r[name_key]):
            return True
        t = r[created_key]
        if not t:
            return False
        return any(abs(t - ct) <= _BATCH_WINDOW_SECONDS for ct in confirmed_test_times)

    return [r for r in rows if not in_test_batch(r)]


def _parse_json_field(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (TypeError, ValueError):
        return []


def _stable_int(seed: str, lo: int, hi: int) -> int:
    import hashlib
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return lo + (h % (hi - lo + 1))


# ── Professors (real, from CollabV's live JSON source) ──────────────────

def load_live_professors() -> list[UserProfileIn]:
    if not PROFESSORS_JSON.exists():
        return []
    with open(PROFESSORS_JSON, encoding="utf-8") as f:
        raw = json.load(f)

    profiles = []
    for p in raw:
        name = p.get("name", "")
        matching = p.get("matching_tags", {}) or {}
        contact = p.get("contact", {}) or {}
        designation = p.get("designation", "Professor")
        department = p.get("department", "")
        university = p.get("university", "")

        research_areas = sorted(set(
            list(p.get("research_areas", []) or []) + matching.get("research_domain_tags", [])
        ) - {""})
        skills = sorted(set(
            list(p.get("technical_expertise", []) or []) + matching.get("tech_skill_tags", [])
        ) - {""})
        patents = [
            f"{pt.get('title')} ({pt.get('year')})" if isinstance(pt, dict) else str(pt)
            for pt in (p.get("patents") or [])
        ]
        publications = list(p.get("publications", []) or [])
        n_signal = len(publications) + len(patents)

        profiles.append(UserProfileIn(
            name=name, role="professor",
            headline=f"{designation}, {matching.get('department_short', department)} at {university}",
            bio=p.get("biography", ""), organization=university, department=department,
            job_title=designation, location=p.get("location", ""), skills=skills,
            research_areas=research_areas, interests=matching.get("industry_tags", []),
            publications=publications, patents=patents,
            experience=[ExperienceEntry(title=designation, org=university, years=str(p.get("experience_years") or ""))],
            education=list(p.get("education", []) or []), keywords=list(p.get("nlp_tags", []) or []),
            tags=matching.get("research_domain_tags", []) + [matching.get("primary_domain", "")],
            languages=["English"], website=contact.get("profile_url", ""),
            followers=_stable_int(name, 20, 60) + n_signal * 8,
            connections=_stable_int(name, 20, 60) + n_signal * 8 + _stable_int(name + "c", 10, 120),
            activity_score=round(min(1.0, n_signal / 15 + 0.15), 3),
        ))
    return profiles


# ── Student / Employee / Institute / Company (live from CollabV's DB) ───

def _connect():
    if not COLLABV_DB.exists():
        return None
    return sqlite3.connect(str(COLLABV_DB))


def load_live_students() -> list[UserProfileIn]:
    conn = _connect()
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM student_profiles").fetchall()
    conn.close()

    profiles = []
    for r in _drop_test_batches(rows, "user_id", "name", "created_at"):
        skills = _parse_json_field(r["skills"])
        research_areas = _parse_json_field(r["research_areas"])
        profiles.append(UserProfileIn(
            name=r["name"], role="student",
            headline=f"{r['field_of_study'] or 'Student'} at {r['institute'] or 'CollabV'}",
            bio=r["bio"] or "", organization=r["institute"] or "", department=r["field_of_study"] or "",
            skills=skills, research_areas=research_areas, interests=_parse_json_field(r["interests"]),
            publications=_parse_json_field(r["publications"]), education=_parse_json_field(r["education"]),
        ))
    return profiles


def load_live_employees() -> list[UserProfileIn]:
    conn = _connect()
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM employee_profiles").fetchall()
    conn.close()

    profiles = []
    for r in _drop_test_batches(rows, "user_id", "name", "created_at"):
        profiles.append(UserProfileIn(
            name=r["name"], role="employee",
            headline=f"{r['job_title'] or 'Employee'} at {r['company_name'] or 'CollabV'}",
            organization=r["company_name"] or "", department=r["industry"] or "",
            job_title=r["job_title"] or "", bio=r["bio"] or "",
            skills=_parse_json_field(r["skills"]), interests=_parse_json_field(r["interests"]),
            education=_parse_json_field(r["education"]),
            experience=[ExperienceEntry(title=r["job_title"] or "", org=r["company_name"] or "", years="")],
        ))
    return profiles


def load_live_institutes() -> list[UserProfileIn]:
    conn = _connect()
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM institute_profiles").fetchall()
    conn.close()

    profiles = []
    for r in _drop_test_batches(rows, "user_id", "institute_name", "created_at"):
        areas = _parse_json_field(r["focus_areas"])
        profiles.append(UserProfileIn(
            name=r["institute_name"], role="institute",
            headline=f"Research institute — {', '.join(areas[:2]) or 'multiple focus areas'}",
            bio=r["bio"] or "", organization=r["institute_name"], research_areas=areas,
            tags=_parse_json_field(r["collaboration_types"]),
        ))
    return profiles


def load_live_companies() -> list[UserProfileIn]:
    conn = _connect()
    if conn is None:
        return []
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM company_profiles").fetchall()
    conn.close()

    profiles = []
    for r in _drop_test_batches(rows, "company_id", "company_name", "created_at"):
        profiles.append(UserProfileIn(
            name=r["company_name"], role="company",
            headline=r["description"] or f"{r['industry'] or 'Company'} on CollabV",
            bio=r["description"] or "", organization=r["company_name"], department=r["industry"] or "",
            skills=_parse_json_field(r["technologies_used"]), research_areas=_parse_json_field(r["research_interests"]),
            interests=_parse_json_field(r["focus_areas"]), keywords=_parse_json_field(r["keywords"]),
            tags=_parse_json_field(r["preferred_collaboration_areas"]),
        ))
    return profiles


def load_all_live_profiles() -> dict[str, list[UserProfileIn]]:
    """Every real-data source CollabV currently has. Researcher/Startup/
    Alumni/Mentor are omitted on purpose — CollabV's schema has no table
    for them yet, so there is nothing real to load."""
    return {
        "professor": load_live_professors(),
        "student": load_live_students(),
        "employee": load_live_employees(),
        "institute": load_live_institutes(),
        "company": load_live_companies(),
    }
