"""
CollabV AI - Demo & Batch Testing (v3)
========================================
Loads 100 companies from Excel, runs the full pipeline with all v3 scoring:
  - Tier 1/2/3 match scores
  - patent_score (Model 3)
  - readiness_score (Model 4)
  - deal_probability (Model 6)

Outputs:
  - collabv_v3_results_100.xlsx (one row per company, wide format with #1..#5 profs)
  - collabv_v3_all_matches.xlsx (long format - one row per match)
  - Console: summary stats + spot-check of companies #1, #25, #50, #75, #100
"""

from __future__ import annotations

import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from collabv.matching_engine import MatchingEngine, CompanyRequest
from collabv.need_parser import parse_need
from collabv.deal_scorer import DealScorer


# ─── Helpers ─────────────────────────────────────────────────────────────────

def safe_str(val, default=""):
    if pd.isna(val):
        return default
    return str(val).strip()


def safe_list(val):
    s = safe_str(val)
    if not s:
        return []
    items = []
    for part in s.replace("|", ";").replace(",", ";").split(";"):
        part = part.strip()
        if part:
            items.append(part)
    return items


def print_header(text: str, char: str = "="):
    width = 78
    print(f"\n{char * width}")
    print(f"  {text}")
    print(f"{char * width}")


# ─── Load companies ────────────────────────────────────────────────────────

def load_companies(excel_path: str) -> list:
    df = pd.read_excel(excel_path, header=2)
    companies = []
    for _, row in df.iterrows():
        desc_parts = [
            safe_str(row.get("Project Description")),
            safe_str(row.get("Challenges")),
            safe_str(row.get("Expected Outcome")),
            safe_str(row.get("Desired Deliverables")),
        ]
        full_description = " ".join(p for p in desc_parts if p)
        companies.append({
            "company_id": safe_str(row.get("Company Id"), f"C-{len(companies)+1}"),
            "company_name": safe_str(row.get("Company Name"), "Unknown"),
            "industry": safe_str(row.get("Industry Sector", row.get("Industry Domain", ""))),
            "technical_area": safe_list(row.get("Technical Area")),
            "required_expertise": safe_list(row.get("Required Expertise")),
            "technology_stack": safe_list(row.get("Technology Stack")),
            "project_description": safe_str(row.get("Project Description")),
            "challenges": safe_str(row.get("Challenges")),
            "collaboration_type": safe_str(row.get("Collaboration Type"), "Joint Research"),
            "research_level": safe_str(row.get("Research Level"), "applied"),
            "location": safe_str(row.get("Location"), "Any"),
            "budget": safe_str(row.get("Budget"), "medium"),
            "timeline": safe_str(row.get("Timeline"), "12 months"),
            "application_area": safe_str(row.get("Application Area")),
            "full_description": full_description,
        })
    return companies


# ─── Build request ─────────────────────────────────────────────────────────

def build_request(company: dict) -> CompanyRequest:
    parsed = parse_need(company["full_description"], use_claude=False)
    parsed_fields = parsed.to_company_request_fields()
    tech_area = company["technical_area"] or parsed_fields["technical_area"]
    expertise = company["required_expertise"] or parsed_fields["required_expertise"]
    tech_stack = company["technology_stack"] or parsed_fields["tech_stack"]
    industry = company["industry"] or parsed_fields["industry"]
    return CompanyRequest(
        company_id=company["company_id"],
        company_name=company["company_name"],
        technical_area=tech_area,
        required_expertise=expertise,
        tech_stack=tech_stack,
        industry=industry,
        project_description=company["project_description"],
        challenges=company["challenges"],
        collaboration_type=company["collaboration_type"] or parsed_fields["collaboration_type"],
        location_preference=company["location"],
        research_level=company["research_level"] or parsed_fields["research_level"],
        budget_tier=company["budget"],
    )


# ─── Spot-check renderer ───────────────────────────────────────────────────

def print_spot_check(company: dict, match, deal, profs_by_id: dict, idx: int) -> None:
    print()
    print("=" * 78)
    print(f"  SPOT-CHECK #{idx}: {company['company_name']}  ({company['industry']})")
    print("=" * 78)
    print(f"  Brief: {company['full_description'][:180]}...")
    print()
    print(f"  TOP MATCH: {match.professor_name}")
    print(f"     Department: {match.department}")
    print()
    print(f"  Composite score   : {match.score:5.1f} / 100")
    print(f"    Tier 1 (kw+dept) : {match.tier1_score:5.1f}  weight=0.45")
    print(f"    Tier 2 (semantic): {match.tier2_score:5.1f}  weight=0.30")
    print(f"    Tier 3 (soft)    : {match.tier3_score:5.1f}  weight=0.05")
    print(f"    Patent score     : {match.patent_score:5.1f}  weight=0.10")
    print(f"    Readiness        : {match.readiness_score:5.1f}  weight=0.10")
    print()
    print(f"  Contextual readiness: {match.contextual_readiness:.1f}")
    print(f"  Deal probability    : {deal.success_percent:.1f}%  ({deal.band}, {deal.confidence_level} confidence)")
    if match.reasons:
        print(f"  Reasons             : {' | '.join(match.reasons[:3])}")
    prof = profs_by_id.get(match.professor_id, {})
    pats = prof.get("patents") or []
    print(f"  Patents on file     : {len(pats)}")
    if pats:
        print(f"     Recent: {pats[0].get('title', '')[:60]}...")
    if deal.risk_factors:
        print(f"  Top risk            : {deal.risk_factors[0].description}")
    if deal.opportunity_factors:
        print(f"  Top opportunity     : {deal.opportunity_factors[0]}")


# ─── Main ──────────────────────────────────────────────────────────────────

def run_demo() -> None:
    print_header("CollabV AI v3 - 100 Company Batch Matching")

    t0 = time.time()
    engine = MatchingEngine(enable_embeddings=False)
    deal_scorer = DealScorer()
    profs_by_id = {str(p.get("professor_id", "")): p for p in engine.professors}
    print(f"  Engine ready: {len(engine.professors)} professors in {time.time()-t0:.1f}s")
    print(f"  Profs with patents: {sum(1 for p in engine.professors if p.get('patents'))}")

    excel_path = Path(__file__).parent.parent / "100_Companies_Collaboration_Schema.xlsx"
    if not excel_path.exists():
        print(f"  ERROR: {excel_path} not found")
        return
    companies = load_companies(str(excel_path))
    print(f"  Loaded {len(companies)} companies\n")

    wide_rows = []           # one row per company
    long_rows = []           # one row per (company, professor) match
    top1_scores = []
    top1_deals = []
    dept_counter = Counter()
    spot_check_indices = {1, 25, 50, 75, 100}
    spot_check_data = []
    total_time = 0.0

    for idx, company in enumerate(companies, start=1):
        request = build_request(company)
        t1 = time.time()
        matches = engine.match(request, top_k=5)

        match_dicts = [
            {
                "professor_id": m.professor_id, "professor_name": m.professor_name,
                "department": m.department, "score": m.score,
                "tier1_score": m.tier1_score, "tier2_score": m.tier2_score, "tier3_score": m.tier3_score,
                "patent_score": m.patent_score, "readiness_score": m.readiness_score,
                "contextual_readiness": m.contextual_readiness,
            }
            for m in matches
        ]
        deals = deal_scorer.batch_score(match_dicts, profs_by_id, request)
        deal_by_pid = {d.professor_id: d for d in deals}
        elapsed = time.time() - t1
        total_time += elapsed

        top = matches[0] if matches else None
        if top:
            top1_scores.append(top.score)
            top1_deals.append(deal_by_pid.get(top.professor_id).success_percent if deal_by_pid.get(top.professor_id) else 0)
            for m in matches:
                dept_counter[m.department] += 1
            print(f"  [{idx:3d}/100] {company['company_name'][:32]:32s} -> "
                  f"{top.professor_name[:24]:24s} {top.department[:24]:24s} "
                  f"score={top.score:5.1f}  deal={deal_by_pid[top.professor_id].success_percent:5.1f}%")

        # Wide row: #1..#5 in one record
        wide = {
            "Company ID": company["company_id"],
            "Company": company["company_name"],
            "Industry": request.industry,
        }
        for i, m in enumerate(matches, start=1):
            d = deal_by_pid.get(m.professor_id)
            wide[f"#{i}_professor"] = m.professor_name
            wide[f"#{i}_department"] = m.department
            wide[f"#{i}_score"] = m.score
            wide[f"#{i}_patent_score"] = m.patent_score
            wide[f"#{i}_readiness"] = m.readiness_score
            wide[f"#{i}_deal_prob"] = d.success_percent if d else 0
            wide[f"#{i}_deal_band"] = d.band if d else ""
        wide_rows.append(wide)

        # Long rows for the detailed sheet
        for rank, m in enumerate(matches, start=1):
            d = deal_by_pid.get(m.professor_id)
            long_rows.append({
                "Company ID": company["company_id"],
                "Company": company["company_name"],
                "Industry": request.industry,
                "Rank": rank,
                "Professor": m.professor_name,
                "Professor ID": m.professor_id,
                "Department": m.department,
                "Match Score": m.score,
                "Tier1": m.tier1_score, "Tier2": m.tier2_score, "Tier3": m.tier3_score,
                "Patent Score": m.patent_score,
                "Readiness": m.readiness_score,
                "Contextual Readiness": m.contextual_readiness,
                "Deal Probability %": d.success_percent if d else 0,
                "Deal Band": d.band if d else "",
                "Risk Count": len(d.risk_factors) if d else 0,
                "Top Risk": d.risk_factors[0].description if d and d.risk_factors else "",
                "Reasons": "; ".join(m.reasons[:3]),
            })

        if idx in spot_check_indices and top:
            spot_check_data.append((idx, company, top, deal_by_pid[top.professor_id]))

    # ─── Save Excel ─────────────────────────────────────────────────────────

    output_path = Path(__file__).parent.parent / "collabv_v3_results_100.xlsx"
    df_wide = pd.DataFrame(wide_rows)
    df_long = pd.DataFrame(long_rows)
    with pd.ExcelWriter(str(output_path), engine="openpyxl") as writer:
        df_wide.to_excel(writer, index=False, sheet_name="Company x Top5 (wide)")
        df_long.to_excel(writer, index=False, sheet_name="All Matches (long)")

        # Summary sheet
        summary_data = []
        if top1_scores:
            summary_data = [
                {"metric": "Companies processed", "value": len(companies)},
                {"metric": "Total matches", "value": len(long_rows)},
                {"metric": "Total time (s)", "value": round(total_time, 1)},
                {"metric": "Avg time/company (s)", "value": round(total_time / len(companies), 2)},
                {"metric": "Top-1 avg score", "value": round(sum(top1_scores) / len(top1_scores), 1)},
                {"metric": "Top-1 avg deal probability %", "value": round(sum(top1_deals) / len(top1_deals), 1)},
                {"metric": "Top-1 max", "value": round(max(top1_scores), 1)},
                {"metric": "Top-1 min", "value": round(min(top1_scores), 1)},
            ]
        pd.DataFrame(summary_data).to_excel(writer, index=False, sheet_name="Summary")

    # ─── Console summary ────────────────────────────────────────────────────

    print_header("Summary Statistics (v3)")
    print(f"  Companies matched         : {len(companies)}")
    print(f"  Total matches             : {len(long_rows)} ({len(companies)} x 5)")
    print(f"  Total match time          : {total_time:.1f}s ({total_time / len(companies):.2f}s per company)")
    print()
    print(f"  Top-1 avg score           : {sum(top1_scores) / len(top1_scores):.1f}")
    print(f"  Top-1 avg deal probability: {sum(top1_deals) / len(top1_deals):.1f}%")
    print(f"  Top-1 max                 : {max(top1_scores):.1f}")
    print(f"  Top-1 min                 : {min(top1_scores):.1f}")
    print(f"  Top-1 median              : {sorted(top1_scores)[len(top1_scores)//2]:.1f}")
    print()

    buckets = {"90+": 0, "80-89": 0, "70-79": 0, "60-69": 0, "50-59": 0, "<50": 0}
    for s in top1_scores:
        if s >= 90: buckets["90+"] += 1
        elif s >= 80: buckets["80-89"] += 1
        elif s >= 70: buckets["70-79"] += 1
        elif s >= 60: buckets["60-69"] += 1
        elif s >= 50: buckets["50-59"] += 1
        else: buckets["<50"] += 1
    print(f"  Top-1 Score Buckets:")
    for bucket, count in buckets.items():
        bar = "#" * count
        print(f"    {bucket:>6s} : {count:3d} {bar}")
    print()

    print(f"  Top Matched Departments:")
    for dept, count in dept_counter.most_common(10):
        bar = "#" * (count // 3)
        print(f"    {dept:45s} {count:3d} {bar}")

    # ─── Spot checks ────────────────────────────────────────────────────────

    print_header("Spot-checks: full scoring breakdown for #1, #25, #50, #75, #100")
    for idx, company, top, deal in spot_check_data:
        print_spot_check(company, top, deal, profs_by_id, idx)

    print()
    print(f"  Results saved to: {output_path}")
    print(f"  Sheet 1: 'Company x Top5 (wide)' - one row per company, 5 profs each")
    print(f"  Sheet 2: 'All Matches (long)'    - one row per match")
    print(f"  Sheet 3: 'Summary'               - aggregate stats")


if __name__ == "__main__":
    run_demo()
