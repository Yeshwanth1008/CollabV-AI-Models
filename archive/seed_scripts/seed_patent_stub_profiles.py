"""
Seed `profile_type: "patent_stub"` profiles for IITM inventors who have granted
patents but aren't in our 543-professor DB.

Per the audit (run earlier this session against iitm_patents.json), 28 unique
inventors carry 122 patents but don't appear in iitm_professors_nlp.json -
they're real IITM faculty (e.g. RAMAPRABHU S with 37 patents) who are retired,
new hires, joint-appointment, or otherwise outside the seed list.

Without these stubs, the draft-listing seeder can't attach their patents to a
professor_id (the listings.professor_id FK would point to nothing).

Strategy:
  1. Backfill profile_type="faculty" on every existing 543 prof (idempotent).
  2. For each unmatched inventor in iitm_patents.json with >=1 GRANTED patent:
     - generate professor_id  = "STUB-<surname>-<random>"
     - synthesize a minimal profile:
         name              <- the inventor's IITM-feed name normalized to
                              title case (LAKSHMAN NEELAKANTAN -> "Lakshman
                              Neelakantan")
         department        <- mode of the inventor's patents' departments
         research_areas    <- domain tags drawn from the patent titles
         biography         <- short note: "Stub profile auto-created from
                              IITM patent feed. Real biography pending."
         patents           <- empty (the draft listing seeder populates these)
         profile_type      <- "patent_stub"
         is_synthetic      <- False (these are real people; just incomplete)
  3. Append stubs to iitm_professors_nlp.json (and the mirror
     iitm_professors_with_patents.json). Back up first.
  4. Write a JSONL audit log at scripts/seed_patent_stubs.audit.jsonl.

The matching engine treats stubs the same as faculty - they just have thin
profiles. The marketplace surfaces a "biography pending" banner on stub-owned
listings in Phase 2.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Reuse the matcher from the existing patent_scraper. It already understands
# IITM name format quirks (CAPS, "SURNAME INITIAL", concatenated middle names).
from collabv.patent_scraper import _match_inventor, _name_tokens  # noqa: E402


PROFS_FILE          = ROOT / "iitm_professors_nlp.json"
PROFS_WITH_PATENTS  = ROOT / "iitm_professors_with_patents.json"
PATENTS_FILE        = ROOT / "iitm_patents.json"
AUDIT_LOG           = Path(__file__).parent / "seed_patent_stubs.audit.jsonl"


# ─── Domain-tag inference from patent titles ─────────────────────────────

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "materials":     ["alloy", "composite", "polymer", "ceramic", "metallurg",
                      "nanomaterial", "thin film", "coating"],
    "biotech":       ["enzyme", "protein", "biopolymer", "drug", "biomarker",
                      "vaccine", "antibody", "gene"],
    "chemicals":     ["catalyst", "polymerization", "synthesis", "reaction"],
    "ai_ml":         ["machine learning", "deep learning", "neural", "ai ",
                      "classification", "detection"],
    "electronics":   ["semiconductor", "vlsi", "circuit", "antenna",
                      "rf ", "5g", "wireless"],
    "energy":        ["solar", "battery", "fuel cell", "hydrogen",
                      "photovoltaic", "energy storage", "renewable"],
    "robotics":      ["robot", "manipulator", "actuator", "gripper",
                      "autonomous"],
    "sensors_iot":   ["sensor", "iot", "wearable", "device"],
    "optics":        ["optical", "photonic", "laser", "lens", "fiber"],
    "mechanical":    ["mechanism", "linkage", "manufacturing", "machining",
                      "structure", "additive"],
    "civil":         ["concrete", "structural", "seismic", "construction"],
    "healthcare":    ["medical", "diagnostic", "implant", "surgical",
                      "rehabilitation", "prosthet"],
}


def _infer_domains(titles: List[str]) -> List[str]:
    text = " ".join(titles).lower()
    return sorted({d for d, keywords in _DOMAIN_KEYWORDS.items()
                   if any(k in text for k in keywords)})


# ─── Name + department helpers ───────────────────────────────────────────

def _title_case_iitm_name(raw: str) -> str:
    """LAKSHMAN NEELAKANTAN -> 'Lakshman Neelakantan'.
    Single letters stay uppercase (initials): MURALEEDHARAN K M -> 'Muraleedharan K M'.
    """
    parts = [p for p in re.split(r"\s+", raw.strip()) if p]
    out = []
    for p in parts:
        if len(p) == 1:
            out.append(p.upper())
        else:
            out.append(p.capitalize())
    return " ".join(out)


def _mode_department(departments: List[str]) -> str:
    counter = Counter(d for d in departments if d)
    if not counter:
        return "Unknown"
    most_common, _ = counter.most_common(1)[0]
    # Normalize to the same format the rest of the DB uses
    if not most_common.startswith("Department of "):
        most_common = f"Department of {most_common}"
    return most_common


def _surname_slug(name: str) -> str:
    """Pick a stable slug from a name for the stub professor_id.
    Drops single-letter initials, takes the longest remaining token.
    """
    tokens = [t for t in _name_tokens(name) if len(t) > 1]
    if not tokens:
        return uuid.uuid4().hex[:6].upper()
    longest = max(tokens, key=len)
    return re.sub(r"[^A-Za-z0-9]", "", longest.upper())[:16]


def _stub_professor_id(raw_name: str, taken: set) -> str:
    base = f"STUB-{_surname_slug(raw_name)}"
    if base not in taken:
        return base
    for i in range(2, 50):
        candidate = f"{base}-{i}"
        if candidate not in taken:
            return candidate
    return f"{base}-{uuid.uuid4().hex[:6].upper()}"


# ─── Main ────────────────────────────────────────────────────────────────

def main() -> Dict[str, Any]:
    # 1. Load existing data
    professors = json.loads(PROFS_FILE.read_text(encoding="utf-8"))
    patents    = json.loads(PATENTS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(professors)} existing professors")
    print(f"Loaded {len(patents)} patents from iitm_patents.json")

    # 2. Backfill profile_type on existing professors (idempotent)
    backfilled = 0
    for p in professors:
        if p.get("profile_type") is None:
            p["profile_type"] = "faculty"
            backfilled += 1
    print(f"Backfilled profile_type='faculty' on {backfilled} existing professors")

    # 3. Group patents by inventor and find unmatched
    by_inventor: Dict[str, List[Dict[str, Any]]] = {}
    for pat in patents:
        inv = (pat.get("inventors") or [""])[0].strip()
        if not inv:
            continue
        by_inventor.setdefault(inv, []).append(pat)

    # ─── Build the "already rescued" inventor set ───────────────────────
    # The interactive rescue script (scripts/rescue_patent_matches.py) tags
    # every patent it merges into a faculty profile with `rescued_via: "<IITM
    # feed name>"`. If we see that tag for an inventor name here, the inventor
    # is already covered by faculty and creating a stub would be a regression.
    # This closes the loop the stale matcher leaves open without requiring the
    # matcher itself to learn new tricks.
    rescued_feed_names: set = set()
    for p in professors:
        for pat in (p.get("patents") or []):
            if isinstance(pat, dict) and pat.get("rescued_via"):
                rescued_feed_names.add(str(pat["rescued_via"]).upper())
    if rescued_feed_names:
        print(f"Detected {len(rescued_feed_names)} inventor name(s) already "
              f"merged by rescue tags - will skip them")

    unmatched: List[tuple[str, List[Dict[str, Any]]]] = []
    for inv, pats in by_inventor.items():
        # Primary check: did the matcher already place this inventor?
        if _match_inventor(inv, professors) is not None:
            continue
        # Secondary check: did the rescue script already place this inventor
        # under a faculty profile? If so the matcher misses it but the
        # rescue tags prove it's covered - skip the stub.
        if inv.upper() in rescued_feed_names:
            continue
        unmatched.append((inv, pats))
    # Only keep inventors with at least one GRANTED patent (we only list granted)
    unmatched = [(inv, [p for p in pats if str(p.get("status", "")).lower() == "granted"])
                 for inv, pats in unmatched]
    unmatched = [(inv, pats) for inv, pats in unmatched if pats]

    print()
    print(f"Found {len(unmatched)} unmatched inventors with >=1 granted patent")
    print(f"Total granted patents under unmatched inventors: "
          f"{sum(len(pats) for _, pats in unmatched)}")
    print()

    # 4. Generate stubs
    taken_ids = {p.get("professor_id") for p in professors}
    stubs: List[Dict[str, Any]] = []
    audit_records: List[Dict[str, Any]] = []
    for raw_name, pats in sorted(unmatched, key=lambda x: -len(x[1])):
        pid = _stub_professor_id(raw_name, taken_ids)
        taken_ids.add(pid)
        titles = [str(p.get("title", "")) for p in pats]
        depts = [str(p.get("department", "")) for p in pats]
        dept = _mode_department(depts)
        domains = _infer_domains(titles)
        stub = {
            "professor_id": pid,
            "name": _title_case_iitm_name(raw_name),
            "department": dept,
            "biography": (
                "Stub profile auto-created from IITM patent feed. Full "
                "biography pending — this inventor holds granted patents but "
                "isn't yet in our faculty directory."
            ),
            "research_areas": domains or ["unspecified"],
            "publications": [],
            "patents": [],  # populated by seed_draft_listings.py
            "experience_years": None,
            "industry_exposure": [],
            "collaboration_history": "",
            "technical_expertise": [],
            "university": "IIT Madras",
            "location": "Chennai",
            "contact": {},
            "education": [],
            "designation": "",
            "seniority_level": "Unknown",
            "matching_tags": {
                "research_domain_tags": domains,
                "tech_skill_tags": [],
                "industry_tags": [],
                "collab_type_tags": [],
                "research_level_tags": ["applied"],
                "primary_domain": domains[0] if domains else "",
            },
            "nlp_tags": domains,
            "domain_scores": {d: 0.5 for d in domains},
            "industry_fit": {},
            "expertise_summary": "",
            "profile_type": "patent_stub",
            "_iitm_feed_name": raw_name,
            "_n_granted_patents_in_feed": len(pats),
        }
        stubs.append(stub)
        audit_records.append({
            "stub_professor_id": pid,
            "name": stub["name"],
            "iitm_feed_name": raw_name,
            "department": dept,
            "granted_patent_count": len(pats),
            "inferred_domains": domains,
            "created_at": time.time(),
        })

    # 5. Back up + write
    PROFS_FILE.replace(PROFS_FILE.with_suffix(".pre-stub-seed.bak.json"))
    # the .replace() above moves PROFS_FILE; restore the read content + add stubs
    all_professors = professors + stubs
    PROFS_FILE.write_text(
        json.dumps(all_professors, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Mirror to iitm_professors_with_patents.json if it exists
    if PROFS_WITH_PATENTS.exists():
        PROFS_WITH_PATENTS.replace(PROFS_WITH_PATENTS.with_suffix(".pre-stub-seed.bak.json"))
        PROFS_WITH_PATENTS.write_text(
            json.dumps(all_professors, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # 6. Audit log
    with open(AUDIT_LOG, "w", encoding="utf-8") as f:
        for rec in audit_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"Wrote {len(stubs)} stub profiles to {PROFS_FILE.name}")
    if PROFS_WITH_PATENTS.exists():
        print(f"  mirrored to {PROFS_WITH_PATENTS.name}")
    print(f"Backup: iitm_professors_nlp.pre-stub-seed.bak.json")
    print(f"Audit:  {AUDIT_LOG}")
    print()
    print("Top 10 stubs by # granted patents:")
    for rec in sorted(audit_records, key=lambda r: -r["granted_patent_count"])[:10]:
        print(f"  +{rec['granted_patent_count']:3d} patents  "
              f"{rec['stub_professor_id']:24s}  ({rec['name']})  "
              f"-> {rec['inferred_domains'][:3]}")

    return {
        "stubs_created": len(stubs),
        "existing_backfilled": backfilled,
        "total_after": len(all_professors),
        "audit_file": str(AUDIT_LOG),
    }


if __name__ == "__main__":
    summary = main()
    print()
    print(json.dumps(summary, indent=2))
