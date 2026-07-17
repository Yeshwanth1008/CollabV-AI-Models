"""
One-shot interactive rescue pass for patent-to-professor matching.

Loads:
  - iitm_patents.json (raw 1437-record IITM feed)
  - iitm_professors_with_patents.json (current matched state)

For each inventor name that the auto-matcher MISSED but is fuzzy-close to a
canonical professor name, prompt the user y/n. Decisions are applied at the
end, after a backup is written.

Hard-coded denylist excludes pairs that look similar but are different people
(Sankararaman/Sankaran, Babji/Balaji, etc.) so they never reach the prompt.

Usage:
    python scripts/rescue_patent_matches.py
    python scripts/rescue_patent_matches.py --auto-high      # apply all HIGH without prompting
    python scripts/rescue_patent_matches.py --dry-run        # show proposals + summary, no writes
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from collabv.patent_scraper import _match_inventor, _name_tokens  # noqa: E402


PATENTS_FILE = ROOT / "iitm_patents.json"
PROFS_FILE   = ROOT / "iitm_professors_with_patents.json"
BACKUP_FILE  = ROOT / "iitm_professors_with_patents.backup-before-rescue.json"


# ─── Denylist (do NOT propose - these LOOK similar but are different people) ─

DENYLIST: set[Tuple[str, str]] = {
    # From the audit's false-positive list
    ("Sankararaman S",       "SANKARAN S"),
    ("Rajesh Kumar",         "RAJNISH KUMAR"),
    ("Ramesh K",             "RAMESH A"),
    ("Srinivasa Rao Manam",  "SRINIVASA RAO BAKSHI"),
    ("Mathava Kumar S",      "SAMPATH KUMAR T S"),
    ("Babji Srinivasan",     "BALAJI SRINIVASAN"),
    ("Balaji Srinivasan",    "BABJI SRINIVASAN"),  # inverse direction
    ("Aravind R",            "ARAVIND G"),       # different surname initial
    ("Arun Kumar G",         "SARAVANA KUMAR G"),# different first name
    ("Ramakrishna M",        "RAMAKRISHNA P A"), # different middle/surname
    ("Srinivasan G",         "SRINIVASAN K"),    # different surname initial
    ("Muraleedharan V.R",    "MURALEEDHARAN K M"),# different initial pair
    ("Srinivasan K",         "SRINIVASA REDDY K"),# different person (Reddy = correct match)
    ("Kasi Viswanathan S",   "B VISWANATHAN"),    # different first/middle initials
    ("Sivakumar K.C",        "HARIKUMAR K C"),    # different first name
    ("Sangaranayanan M.V",   "SANKARANARAYANAN V"),# different spelling root
    ("Sankararaman S",       "SANKARANARAYANAN V"),
}


# ─── Scoring + classification ─────────────────────────────────────────────

HIGH_THRESHOLD   = 1.05   # spacing / case / single-letter typo - safe default Y
MEDIUM_THRESHOLD = 0.85   # plausible variant - default N, needs eyeball


def _fuzzy_score(a: str, b: str) -> Tuple[float, int]:
    """SequenceMatcher ratio + shared-token bonus. Returns (score, shared)."""
    a_toks = set(_name_tokens(a))
    b_toks = set(_name_tokens(b))
    shared = len(a_toks & b_toks)
    ratio = SequenceMatcher(None, a.lower(), b.lower()).ratio()
    return ratio + 0.10 * shared, shared


def _classify_reason(prof_name: str, inv_name: str,
                     score: float, shared: int) -> Tuple[str, str]:
    """Return (confidence_label, human_reason)."""
    p, i = prof_name.lower(), inv_name.lower()
    p_compact = "".join(p.split())
    i_compact = "".join(i.split())
    p_alpha = "".join(c for c in p if c.isalnum())
    i_alpha = "".join(c for c in i if c.isalnum())

    if p_compact == i_compact:
        return "HIGH", "spacing-only difference"
    if p_alpha == i_alpha:
        return "HIGH", "punctuation / case only"
    if score >= 1.15 and shared >= 2:
        return "HIGH", "identical token set, near-identical spelling"
    if score >= 1.05 and shared >= 1:
        # Check for single-letter typo
        diffs = sum(1 for a, b in zip(p_alpha, i_alpha) if a != b)
        diffs += abs(len(p_alpha) - len(i_alpha))
        if diffs <= 2:
            return "HIGH", f"~{diffs}-letter transliteration variant"
        return "HIGH", "strong overlap"
    if score >= MEDIUM_THRESHOLD:
        return "MEDIUM", "plausible variant - please verify"
    return "LOW", "weak match"


# ─── Proposal generation ──────────────────────────────────────────────────

@dataclass
class Proposal:
    professor_idx: int
    professor_name: str
    department: str
    feed_name: str
    patent_count: int
    score: float
    shared: int
    confidence: str          # HIGH / MEDIUM
    reason: str
    sample_titles: List[str]


def build_proposals(patents: List[dict], professors: List[dict]) -> List[Proposal]:
    # 1. Group patents by raw inventor name
    by_inventor: Dict[str, List[dict]] = defaultdict(list)
    for pt in patents:
        inv = (pt.get("inventors") or [""])[0].strip()
        if inv:
            by_inventor[inv].append(pt)

    # 2. Pull all currently-unmatched inventors
    unmatched: List[str] = []
    for inv in by_inventor:
        if _match_inventor(inv, professors) is None:
            unmatched.append(inv)

    # 3. For each unmatched inventor, find best fuzzy prof candidate
    # Also iterate the OTHER direction: scan each prof and its best feed name.
    # Keep the strongest score for any prof-feed pair we propose.
    pair_scores: Dict[Tuple[int, str], Tuple[float, int]] = {}

    # Direction 1: unmatched inventor -> best prof
    for inv in unmatched:
        best_score, best_shared, best_idx = 0.0, 0, -1
        for i, p in enumerate(professors):
            score, shared = _fuzzy_score(p.get("name", ""), inv)
            if score > best_score:
                best_score, best_shared, best_idx = score, shared, i
        if best_idx >= 0 and best_score >= MEDIUM_THRESHOLD:
            key = (best_idx, inv)
            existing = pair_scores.get(key)
            if existing is None or best_score > existing[0]:
                pair_scores[key] = (best_score, best_shared)

    # Direction 2: each prof -> best unmatched inventor (catches the reverse case)
    for i, p in enumerate(professors):
        best_score, best_shared, best_inv = 0.0, 0, None
        for inv in unmatched:
            score, shared = _fuzzy_score(p.get("name", ""), inv)
            if score > best_score:
                best_score, best_shared, best_inv = score, shared, inv
        if best_inv and best_score >= MEDIUM_THRESHOLD:
            key = (i, best_inv)
            existing = pair_scores.get(key)
            if existing is None or best_score > existing[0]:
                pair_scores[key] = (best_score, best_shared)

    # 4. Convert to Proposal objects, applying denylist + classification
    proposals: List[Proposal] = []
    for (idx, inv), (score, shared) in pair_scores.items():
        prof = professors[idx]
        prof_name = prof.get("name", "")
        if (prof_name, inv) in DENYLIST:
            continue
        confidence, reason = _classify_reason(prof_name, inv, score, shared)
        if confidence == "LOW":
            continue
        sample_titles = [pt.get("title", "")[:70]
                         for pt in by_inventor[inv][:2]]
        proposals.append(Proposal(
            professor_idx=idx,
            professor_name=prof_name,
            department=prof.get("department", "").replace("Department of ", ""),
            feed_name=inv,
            patent_count=len(by_inventor[inv]),
            score=score,
            shared=shared,
            confidence=confidence,
            reason=reason,
            sample_titles=sample_titles,
        ))

    # 5. Each feed inventor should be claimed by AT MOST one professor.
    # Keep the highest-scoring proposal per feed name.
    by_feed: Dict[str, Proposal] = {}
    for prop in proposals:
        existing = by_feed.get(prop.feed_name)
        if existing is None or prop.score > existing.score:
            by_feed[prop.feed_name] = prop
    proposals = list(by_feed.values())

    # 6. Sort: HIGH first, then by patent count desc
    proposals.sort(key=lambda p: (0 if p.confidence == "HIGH" else 1,
                                   -p.patent_count))
    return proposals


# ─── Interactive prompt ───────────────────────────────────────────────────

def prompt_decision(proposal: Proposal, idx: int, total: int,
                    auto_high: bool) -> str:
    """Returns 'y', 'n', or 'q'."""
    default = "y" if proposal.confidence == "HIGH" else "n"
    if auto_high and proposal.confidence == "HIGH":
        return "y"

    yn_prompt = ("[Y/n/q]" if default == "y" else "[y/N/q]")
    print()
    print(f"  [{idx:>3d}/{total}]  {proposal.confidence} confidence")
    print(f"  Professor          : {proposal.professor_name}  ({proposal.department})")
    print(f"  IITM feed name     : {proposal.feed_name}")
    print(f"  # patents to attach: {proposal.patent_count}")
    print(f"  Reason             : {proposal.reason}")
    print(f"  Fuzzy score        : {proposal.score:.2f}  (shared tokens: {proposal.shared})")
    if proposal.sample_titles:
        print(f"  Sample patent      : {proposal.sample_titles[0]}")
    try:
        raw = input(f"  Apply? {yn_prompt}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "q"
    if raw in ("q", "quit", "exit"):
        return "q"
    if raw == "":
        return default
    if raw[0] == "y":
        return "y"
    if raw[0] == "n":
        return "n"
    return default


# ─── Apply + persist ──────────────────────────────────────────────────────

def apply_decisions(professors: List[dict], patents: List[dict],
                    accepted: List[Proposal]) -> Dict[str, Any]:
    by_inventor: Dict[str, List[dict]] = defaultdict(list)
    for pt in patents:
        inv = (pt.get("inventors") or [""])[0].strip()
        if inv:
            by_inventor[inv].append(pt)

    per_prof_added: Dict[str, int] = {}
    total_added = 0
    for prop in accepted:
        p = professors[prop.professor_idx]
        # Ensure patents list exists
        if not isinstance(p.get("patents"), list):
            p["patents"] = []
        existing_keys = {
            (pt.get("patent_number"), pt.get("title"))
            for pt in p["patents"]
        }
        added = 0
        for pt in by_inventor[prop.feed_name]:
            key = (pt.get("patent_number"), pt.get("title"))
            if key in existing_keys:
                continue
            # Mark these as "rescued" by the interactive pass
            pt = dict(pt)  # copy, don't mutate the raw feed
            pt["rescued_via"] = prop.feed_name
            p["patents"].append(pt)
            existing_keys.add(key)
            added += 1
        per_prof_added[p["name"]] = per_prof_added.get(p["name"], 0) + added
        total_added += added
    return {"total_added": total_added, "per_prof_added": per_prof_added}


# ─── Main ─────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto-high", action="store_true",
                        help="Apply all HIGH-confidence merges without prompting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print proposals + summary; do NOT write or backup")
    args = parser.parse_args()

    if not PATENTS_FILE.exists():
        print(f"ERROR: {PATENTS_FILE} not found"); sys.exit(2)
    if not PROFS_FILE.exists():
        print(f"ERROR: {PROFS_FILE} not found"); sys.exit(2)

    patents = json.loads(PATENTS_FILE.read_text(encoding="utf-8"))
    professors = json.loads(PROFS_FILE.read_text(encoding="utf-8"))
    print(f"Loaded {len(patents)} patents, {len(professors)} professors")

    proposals = build_proposals(patents, professors)
    high = [p for p in proposals if p.confidence == "HIGH"]
    med  = [p for p in proposals if p.confidence == "MEDIUM"]
    total_proposed_patents = sum(p.patent_count for p in proposals)
    print(f"Proposals: {len(high)} HIGH + {len(med)} MEDIUM "
          f"= {len(proposals)} total ({total_proposed_patents} patents in play)")

    if args.dry_run:
        print()
        print("DRY RUN - listing proposals and exiting (no writes)")
        for i, p in enumerate(proposals, 1):
            print(f"  [{i:>3d}] {p.confidence:>6s}  "
                  f"{p.professor_name[:28]:28s} <- {p.feed_name[:28]:28s}  "
                  f"pat={p.patent_count:3d}  score={p.score:.2f}  ({p.reason})")
        sys.exit(0)

    print()
    print("=" * 76)
    print("Interactive review - press Enter for default, q to quit at any time")
    print("=" * 76)

    accepted: List[Proposal] = []
    skipped: List[Proposal] = []
    quit_early = False

    for i, prop in enumerate(proposals, 1):
        decision = prompt_decision(prop, i, len(proposals), args.auto_high)
        if decision == "q":
            quit_early = True
            print()
            print("Quitting - decisions made so far will still be applied.")
            break
        elif decision == "y":
            accepted.append(prop)
        else:
            skipped.append(prop)

    if not accepted:
        print()
        print("No merges accepted - nothing to write.")
        return

    # Backup
    print()
    print(f"Backing up {PROFS_FILE.name} -> {BACKUP_FILE.name}")
    shutil.copy(PROFS_FILE, BACKUP_FILE)

    # Apply
    result = apply_decisions(professors, patents, accepted)

    # Write
    with open(PROFS_FILE, "w", encoding="utf-8") as f:
        json.dump(professors, f, indent=2, ensure_ascii=False)
    print(f"Wrote {PROFS_FILE.name}")

    # Report
    print()
    print("=" * 76)
    print("Rescue summary")
    print("=" * 76)
    print(f"  Proposals reviewed       : {len(accepted) + len(skipped)}")
    print(f"  Accepted                 : {len(accepted)}")
    print(f"  Skipped                  : {len(skipped)}")
    if quit_early:
        print(f"  Quit early - not reviewed: {len(proposals) - len(accepted) - len(skipped)}")
    print(f"  Patents recovered        : {result['total_added']}")
    print()
    print("  New per-professor counts (top 20):")
    sorted_adds = sorted(result["per_prof_added"].items(),
                        key=lambda x: -x[1])[:20]
    for name, added in sorted_adds:
        prof = next((p for p in professors if p.get("name") == name), None)
        new_total = len(prof.get("patents") or []) if prof else added
        print(f"    +{added:3d}   (new total: {new_total:3d})   {name}")
    print()
    print(f"  Backup     : {BACKUP_FILE}")
    print(f"  Updated    : {PROFS_FILE}")
    print()
    print("  Next step: copy the updated file over iitm_professors_nlp.json")
    print("  to make the matching engine pick it up, then restart the backend.")


if __name__ == "__main__":
    main()
