"""
Validate that retrained weights produce different match outcomes than defaults.

For "AI for autonomous vehicles":
  - Run match with default weights
  - Run match with retrained weights from collabv_weights.json
  - Print side-by-side top-5 and call out who moved up/down

Then exercise the rollback path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from collabv.matching_engine import MatchingEngine, CompanyRequest
from collabv.retrainer import (
    DEFAULT_WEIGHTS, WeightRetrainer, load_weights, save_weights,
)


WEIGHTS_FILE = Path("collabv_weights.json")
DB_PATH = str(Path("collabv_data.db"))


def run_with(engine, weights, req):
    engine.factor_weights = weights
    return engine.match(req, top_k=5)


def render(label, results, weights):
    print(f"\n  {label} (weights tier1={weights['tier1_score']:.2f}, tier2={weights['tier2_score']:.2f}, "
          f"tier3={weights['tier3_score']:.2f}, patent={weights['patent_score']:.2f}, "
          f"readiness={weights['readiness_score']:.2f})")
    print("  Rank  Score   Professor                          Department")
    for i, r in enumerate(results, start=1):
        print(f"  {i:<5} {r.score:5.1f}   {r.professor_name[:32]:32s}   {r.department}")


def side_by_side(old_results, new_results):
    print("\n  === Side-by-side: professor_id, old_rank vs new_rank ===")
    old_ranks = {r.professor_id: i for i, r in enumerate(old_results, start=1)}
    new_ranks = {r.professor_id: i for i, r in enumerate(new_results, start=1)}
    all_ids = set(old_ranks) | set(new_ranks)
    for pid in all_ids:
        old = old_ranks.get(pid, 99)
        new = new_ranks.get(pid, 99)
        name = next((r.professor_name for r in (old_results + new_results) if r.professor_id == pid), pid)
        movement = ""
        if old == 99:
            movement = "NEW IN TOP-5"
        elif new == 99:
            movement = "DROPPED OUT"
        elif new < old:
            movement = f"UP {old - new}"
        elif new > old:
            movement = f"DOWN {new - old}"
        else:
            movement = "same"
        print(f"   {name[:30]:30s}  old=#{old if old<99 else '-'}  new=#{new if new<99 else '-'}  {movement}")


def main():
    print("=" * 78)
    print("  Retraining impact test")
    print("=" * 78)

    print("\nLoading engine + retrained weights...")
    engine = MatchingEngine(enable_embeddings=False)
    retrained = load_weights(WEIGHTS_FILE)
    print(f"  Loaded weights from {WEIGHTS_FILE}")
    print(f"  Default : {DEFAULT_WEIGHTS}")
    print(f"  Trained : {retrained}")

    req = CompanyRequest(
        company_id="TEST-AV",
        company_name="AutonomyLabs",
        technical_area=["computer vision", "autonomous driving"],
        industry="Automotive",
        required_expertise=["3D perception", "object detection"],
        tech_stack=["Python", "PyTorch"],
        project_description="AI for autonomous vehicles - perception, planning, control for L3 driving in Indian conditions.",
        challenges="Sensor noise; adverse weather robustness",
        collaboration_type="Joint Research",
        research_level="applied",
        timeline_months=18,
    )

    old_results = run_with(engine, dict(DEFAULT_WEIGHTS), req)
    new_results = run_with(engine, retrained, req)

    render("OLD WEIGHTS (defaults)", old_results, DEFAULT_WEIGHTS)
    render("NEW WEIGHTS (retrained)", new_results, retrained)
    side_by_side(old_results, new_results)

    # Verify weights file content
    print("\n  collabv_weights.json content:")
    print("    " + WEIGHTS_FILE.read_text().replace("\n", "\n    "))

    # ─── Rollback ──────────────────────────────────────────────────────────
    print()
    print("=" * 78)
    print("  Rollback test")
    print("=" * 78)

    rt = WeightRetrainer(DB_PATH, WEIGHTS_FILE)
    history = rt.get_weight_history(limit=5)
    print(f"\n  Weight history rows: {len(history)}")
    for h in history:
        print(f"    {h['applied_at']:10}  improvement={h['improvement_score']}  note={h['note']}")

    if len(history) >= 2:
        rolled = rt.rollback()
        print(f"\n  Rolled back to: {rolled}")
        after_rollback = load_weights(WEIGHTS_FILE)
        print(f"  Reloaded from disk: {after_rollback}")
        engine.factor_weights = after_rollback
        rb_results = engine.match(req, top_k=3)
        print("\n  Top-3 after rollback:")
        for i, r in enumerate(rb_results, start=1):
            print(f"    {i}  {r.score:5.1f}   {r.professor_name[:30]:30s}   {r.department}")
    else:
        # Manually save a "v1 snapshot" so we have history to roll back from
        print("\n  Not enough history for rollback. Saving an extra snapshot to test...")
        save_weights(DEFAULT_WEIGHTS, WEIGHTS_FILE)
        rt._save_history(DEFAULT_WEIGHTS, 0.0, 0, note="reset for rollback test")
        rolled = rt.rollback()
        print(f"  Rolled back to: {rolled}")


if __name__ == "__main__":
    main()
