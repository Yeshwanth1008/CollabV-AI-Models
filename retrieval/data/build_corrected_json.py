"""
One-off adapter: converts CollabV's iitm_professors_nlp.json (snake_case
schema) into the field names retrieval/indexer.py expects (Title Case
schema), so the retrieval system has real data to index.

COLLABV_ROOT defaults to three directories up from this file (data/ ->
retrieval/ -> repo root), which is already correct once this code is
deployed inside CollabV's own repo. Override via the COLLABV_ROOT env var
for other layouts (e.g. local development where this service's repo and
CollabV's repo are checked out as siblings).
"""
import json
import os
from pathlib import Path

COLLABV_ROOT = Path(os.getenv("COLLABV_ROOT", str(Path(__file__).resolve().parent.parent.parent)))
SRC = COLLABV_ROOT / os.getenv("PROFESSORS_FILE", "iitm_professors_nlp.json")
DST = Path(__file__).resolve().parent / "iitm_professors_final_corrected.json"


def convert(p: dict) -> dict:
    contact = p.get("contact", {}) or {}
    return {
        "name": p.get("name", ""),
        "department": p.get("department", ""),
        "designation": p.get("designation", ""),
        "Research Interests": p.get("research_areas", []),
        "Areas of expertise": p.get("technical_expertise", []),
        "Courses Taught": [],
        "Most recently published papers or publications": p.get("publications", []),
        "Education": p.get("education", []),
        "Email Address": contact.get("email", ""),
        "Phone Number": contact.get("phone", ""),
        "profile_url": contact.get("profile_url", ""),
    }


def main():
    with open(SRC, encoding="utf-8") as f:
        professors = json.load(f)
    converted = [convert(p) for p in professors]
    with open(DST, "w", encoding="utf-8") as f:
        json.dump(converted, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(converted)} professors to {DST}")


if __name__ == "__main__":
    main()
