"""
End-to-end tests for Claude-API-backed features:
  - MatchExplainer (explanation generation + caching)
  - ContractParser (template gen + parse roundtrip + diff)

Runs against the real Claude API when ANTHROPIC_API_KEY is set,
otherwise exercises the rule-based fallback path so we still verify wiring.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from collabv.matching_engine import MatchingEngine, CompanyRequest
from collabv.explainer import MatchExplainer, init_explanation_cache
from collabv.contract_nlp import ContractParser


HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
DB_PATH = str(Path(__file__).parent / "collabv_test.db")


def header(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def test_explainer():
    header("1. MatchExplainer end-to-end")
    print(f"  ANTHROPIC_API_KEY set: {HAS_KEY}")
    if not HAS_KEY:
        print("  -> Will exercise the rule-based fallback path (no API call)")

    init_explanation_cache(DB_PATH)

    # Real match: 'autonomous vehicle perception'
    engine = MatchingEngine(enable_embeddings=False)
    req = CompanyRequest(
        company_id="TEST-AV",
        company_name="AutonomyLabs",
        technical_area=["computer vision", "deep learning", "autonomous driving"],
        industry="Automotive",
        required_expertise=["3D perception", "object detection", "sensor fusion"],
        project_description=(
            "We need ML expertise for autonomous vehicle perception systems. "
            "Our existing pipeline handles 2D detection but we want to add 3D "
            "perception, sensor fusion across camera and LiDAR, and improved "
            "robustness in adverse weather conditions."
        ),
        challenges="Sensor noise in fog/rain; real-time inference under 50ms; edge deployment",
        collaboration_type="Joint Research",
        research_level="applied",
        timeline_months=18,
    )

    matches = engine.match(req, top_k=3)
    profs_by_id = {str(p.get("professor_id", "")): p for p in engine.professors}
    top = matches[0]
    prof = profs_by_id[top.professor_id]
    print(f"  Top match: {top.professor_name}  ({top.department}, score {top.score})")

    explainer = MatchExplainer(db_path=DB_PATH, use_claude=True)
    match_dict = {
        "professor_id": top.professor_id, "professor_name": top.professor_name,
        "department": top.department, "score": top.score,
        "tier1_score": top.tier1_score, "tier2_score": top.tier2_score,
        "tier3_score": top.tier3_score, "patent_score": top.patent_score,
        "readiness_score": top.readiness_score, "reasons": top.reasons,
    }

    # First call: should hit Claude (or fall back to rule-based)
    t0 = time.time()
    exp1 = explainer.explain_match(prof, req, match_dict)
    t1 = time.time() - t0
    print(f"\n  First call: source={exp1.source}, took {t1:.2f}s")
    print(f"  Summary: {exp1.summary}")
    print(f"  Strengths ({len(exp1.key_strengths)}):")
    for s in exp1.key_strengths:
        print(f"     - {s}")
    print(f"  Gaps ({len(exp1.potential_gaps)}):")
    for g in exp1.potential_gaps:
        print(f"     - {g}")
    print(f"  Talking points ({len(exp1.suggested_talking_points)}):")
    for tp in exp1.suggested_talking_points:
        print(f"     - {tp}")

    # Second call: should be served from cache
    t0 = time.time()
    exp2 = explainer.explain_match(prof, req, match_dict)
    t2 = time.time() - t0
    print(f"\n  Second call (cache test): source={exp2.source}, took {t2:.3f}s")
    cache_hit = exp2.source == "cache"
    print(f"  Cache hit: {cache_hit}")

    if HAS_KEY and exp1.source == "rule":
        print("  WARN: Had API key but fell back to rule-based. Possible reasons:")
        print("        - SDK error, model not found, timeout, or invalid response.")

    return exp1, cache_hit


def test_contract_roundtrip():
    header("2. Contract template + parse roundtrip")
    cp = ContractParser(use_claude=True)
    print(f"  Claude enabled: {HAS_KEY}")

    # Generate JRA
    print("\n  Generating Joint Research Agreement...")
    text = cp.generate_template(
        collab_type="joint_research",
        company_name="TechNova AI",
        professor_name="Dr. Balaraman Ravindran",
        department="Computer Science and Engineering",
        research_area="Reinforcement Learning for Robotics",
        amount=2500000,
        start_date="2026-06-01",
        end_date="2027-06-01",
    )
    print(f"  Generated: {len(text)} chars")
    print(f"  First 200 chars:\n     {text[:200].replace(chr(10), ' ')}")

    # Parse roundtrip
    print("\n  Parsing generated contract back...")
    t0 = time.time()
    terms = cp.parse(text)
    t1 = time.time() - t0
    print(f"  Parsed in {t1:.2f}s")
    print(f"  Total amount    : {terms.financial.total_amount:,.0f} {terms.financial.currency}")
    print(f"  Start date      : {terms.timeline.start_date}")
    print(f"  End date        : {terms.timeline.end_date}")
    print(f"  Duration months : {terms.timeline.duration_months}")
    print(f"  Governing law   : {terms.governing_law}")
    print(f"  Parties         : {[p.name for p in terms.parties]}")
    print(f"  IP split        : {terms.ip_terms.ownership_split}")
    print(f"  IP licensing    : {terms.ip_terms.licensing_rights}")
    print(f"  Notice (days)   : {terms.termination.notice_period_days}")
    print(f"  NDA (months)    : {terms.confidentiality.nda_duration_months}")
    if terms.scope.objectives:
        print(f"  Objectives ({len(terms.scope.objectives)}):")
        for o in terms.scope.objectives[:3]:
            print(f"     - {o}")
    if terms.scope.deliverables:
        print(f"  Deliverables ({len(terms.scope.deliverables)}):")
        for d in terms.scope.deliverables[:3]:
            print(f"     - {d}")
    if terms.needs_review:
        print(f"  Needs review    : {terms.needs_review}")

    return text, terms


def test_contract_compare(jra_text: str):
    header("3. Contract diff (JRA vs Advisory)")
    cp = ContractParser(use_claude=False)
    advisory = cp.generate_template(
        collab_type="advisory",
        company_name="TechNova AI",
        professor_name="Dr. Balaraman Ravindran",
        department="Computer Science and Engineering",
        research_area="Reinforcement Learning for Robotics",
        amount=500000,
        start_date="2026-06-01",
        end_date="2026-12-01",
        hours_per_month=10,
    )
    a = cp.parse(jra_text)
    b = cp.parse(advisory)
    diff = cp.compare(a, b)
    print(f"  Changed fields: {len(diff.changed_fields)}")
    for fd in diff.changed_fields[:8]:
        print(f"     {fd.field_name:<35} {str(fd.value_a)[:25]:<25} -> {str(fd.value_b)[:25]:<25} [{fd.significance}]")
    print(f"\n  Risk assessment: {diff.risk_assessment}")


def test_five_explanations():
    header("4. Five sample matches with full explanations")
    descriptions = [
        ("Water purification", "Water purification membrane technology for rural India. Low-cost desalination membranes targeting brackish groundwater in Tamil Nadu villages. Maintenance-free, off-grid operation."),
        ("Drug discovery", "AI-powered drug discovery for rare genetic diseases. Need molecular dynamics simulations and ML-based screening of compound libraries. Focus on protein-protein interaction modulators."),
        ("Supply chain blockchain", "Blockchain-based supply chain tracking for automotive parts. Tier-2 supplier visibility, counterfeit detection, smart contracts for milestone-based payments."),
        ("Bridge monitoring", "Structural health monitoring of bridges using IoT sensors. Vibration-based damage detection, low-power LoRa sensor networks, edge ML for anomaly detection."),
        ("Tamil NLP", "Natural language processing for Tamil and Hindi legal documents. Entity extraction, case-law summarization, low-resource transformer training."),
    ]

    engine = MatchingEngine(enable_embeddings=False)
    explainer = MatchExplainer(db_path=DB_PATH, use_claude=True)
    profs_by_id = {str(p.get("professor_id", "")): p for p in engine.professors}

    for label, desc in descriptions:
        print(f"\n  -- {label} --")
        req = CompanyRequest(
            company_id=f"T-{label[:6]}", company_name=label,
            technical_area=[], required_expertise=[], tech_stack=[],
            industry="", project_description=desc, challenges="",
            collaboration_type="Joint Research", research_level="applied",
            timeline_months=12,
        )
        matches = engine.match(req, top_k=1)
        top = matches[0]
        prof = profs_by_id[top.professor_id]
        match_dict = {
            "professor_id": top.professor_id, "professor_name": top.professor_name,
            "department": top.department, "score": top.score,
            "tier1_score": top.tier1_score, "tier2_score": top.tier2_score,
            "tier3_score": top.tier3_score, "patent_score": top.patent_score,
            "readiness_score": top.readiness_score, "reasons": top.reasons,
        }
        exp = explainer.explain_match(prof, req, match_dict)
        print(f"     Top: {top.professor_name} ({top.department}) score={top.score}")
        print(f"     Source: {exp.source}")
        print(f"     Summary: {exp.summary}")
        if exp.key_strengths:
            print(f"     Top strength: {exp.key_strengths[0]}")


def main():
    print(f"\nClaude integration test - {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Working dir: {Path.cwd()}")

    exp1, cache_hit = test_explainer()
    jra_text, terms = test_contract_roundtrip()
    test_contract_compare(jra_text)
    test_five_explanations()

    header("PASS/FAIL summary")
    print(f"  Explainer first call:  {'PASS' if exp1.summary else 'FAIL'}")
    print(f"  Explainer caching   :  {'PASS' if cache_hit else 'FAIL'}")
    print(f"  Contract gen+parse  :  {'PASS' if terms.financial.total_amount else 'FAIL'}")
    print(f"  Used Claude API     :  {'YES' if exp1.source == 'claude' else 'NO (rule-based fallback)'}")
    if not HAS_KEY:
        print()
        print("  To enable real Claude calls, set ANTHROPIC_API_KEY in your .env file:")
        print("     echo ANTHROPIC_API_KEY=sk-ant-... > .env")


if __name__ == "__main__":
    main()
