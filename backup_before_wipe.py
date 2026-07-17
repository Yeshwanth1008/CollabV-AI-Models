"""
One-off safety backup before wipe_all_data.py runs.

Copies the live SQLite DB and the four seed files being retired (professor
directory, patents-embedded variant, company Excel sheet, problem-statement
compendium) into backups/pre-wipe-<timestamp>/ - a static snapshot on top of
"leave the originals on disk untouched," since this session already saw core
files silently renamed/deleted once by something outside this session.

Not wired into any automated flow - run manually, once, right before
wipe_all_data.py.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "collabv_data.db"

SEED_FILES = [
    ROOT / "iitm_professors_nlp.json",
    ROOT / "iitm_professors_with_patents.json",
    ROOT / "100_Companies_Collaboration_Schema.xlsx",
    ROOT / "collabv" / "data" / "problem_statements.json",
]

# Category tables (the 9 named entity types) - row counts reported for the manifest.
_TABLES_TO_REPORT = [
    "student_profiles", "employee_profiles", "institute_profiles", "company_profiles",
    "professor_profiles", "patent_listings", "problem_statements", "job_postings",
    "research_opportunities",
]


def main() -> None:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / "backups" / f"pre-wipe-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    manifest_lines = [f"Backup created: {timestamp}", ""]

    if DB_PATH.exists():
        shutil.copy2(DB_PATH, backup_dir / DB_PATH.name)
        manifest_lines.append(f"Copied DB: {DB_PATH.name}")

        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        manifest_lines.append("\nRow counts at backup time (the 9 named entity categories):")
        for table in _TABLES_TO_REPORT:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                manifest_lines.append(f"  {table}: {count}")
            except sqlite3.OperationalError:
                manifest_lines.append(f"  {table}: (table not found)")
        conn.close()
    else:
        manifest_lines.append(f"WARNING: {DB_PATH.name} not found - nothing to copy.")

    manifest_lines.append("\nSeed files copied:")
    for f in SEED_FILES:
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)
            manifest_lines.append(f"  {f.name} ({f.stat().st_size:,} bytes)")
        else:
            manifest_lines.append(f"  {f.name}: NOT FOUND (skipped)")

    manifest_path = backup_dir / "MANIFEST.txt"
    manifest_path.write_text("\n".join(manifest_lines), encoding="utf-8")

    print("\n".join(manifest_lines))
    print(f"\nBackup written to: {backup_dir}")


if __name__ == "__main__":
    main()
