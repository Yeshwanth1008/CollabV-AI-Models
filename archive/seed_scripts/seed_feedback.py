"""
Seed realistic feedback records for retraining validation.

For 30 diverse company descriptions:
  - Run matching, top 5 results
  - Per match, simulate feedback with score-based acceptance bias:
       score > 80    -> 75% accept / 25% reject
       60 <= s <= 80 -> 50% accept / 50% reject
       score < 60    -> 25% accept / 75% reject
  - Reject reasons sampled from a realistic pool.

Saves a match_result row first (the retrainer needs the match record to
extract per-factor scores) then a feedback row per simulated decision.

Usage:
    python seed_feedback.py [--db PATH] [--reset]
"""
from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from collabv import database as db
from collabv.matching_engine import MatchingEngine, CompanyRequest


COMPANY_BRIEFS = [
    ("FinTrack Pro", "Fintech", "ML-based fraud detection for UPI payments with sub-100ms decision latency."),
    ("HealthScan AI", "Healthtech", "Computer vision platform for early diabetic retinopathy screening from smartphone fundus images."),
    ("CropYield AI", "Agritech", "Crop yield prediction using satellite imagery and weather forecasts for small farmers."),
    ("LearnLoop Edu", "Edtech", "Personalized adaptive learning paths for K-12 STEM using student interaction data."),
    ("PureH2O Systems", "Cleantech", "Low-energy membrane desalination for off-grid coastal villages."),
    ("FactoryEye Vision", "Manufacturing", "Real-time defect detection on production lines using edge ML on 4K cameras."),
    ("DefenseShield", "Defense", "Drone swarm coordination algorithms for perimeter surveillance."),
    ("BatteryNext", "Cleantech", "Solid-state battery materials research for EV applications."),
    ("LogiRoute AI", "Logistics", "Multi-stop route optimization for last-mile delivery in dense Indian cities."),
    ("RetailPulse", "Retail", "Demand forecasting and dynamic pricing for fashion retail."),
    ("CodeReview AI", "IT", "LLM-powered code review assistant for enterprise dev teams."),
    ("StructSense", "Construction", "IoT-based structural health monitoring for highway bridges."),
    ("MedDevice India", "Medical Devices", "Wearable continuous glucose monitor with low-power BLE."),
    ("AutoDrive India", "Automotive", "Perception stack for L3 autonomous driving in Indian traffic conditions."),
    ("SolarOptima", "Energy", "MPPT controllers and grid-tied inverter design for solar farms."),
    ("AgriDrone", "Agritech", "Drone-based precision spraying with computer vision crop health analysis."),
    ("PharmaSynth", "Pharma", "Continuous flow chemistry for active pharmaceutical ingredient synthesis."),
    ("WaterCycle", "Cleantech", "Industrial wastewater treatment using novel catalyst-functionalized membranes."),
    ("SteelTech", "Materials", "High-strength low-alloy steel development for automotive applications."),
    ("AeroLite", "Aerospace", "Carbon-fiber composite design for UAV airframes."),
    ("LegalLens AI", "Legaltech", "NLP for Indian case law analysis and precedent extraction."),
    ("VoiceFirst India", "Conversational AI", "Tamil and Hindi speech recognition for rural voice interfaces."),
    ("CyberShield", "Cybersecurity", "Anomaly detection in OT/ICS networks using physics-informed ML."),
    ("BuildGreen", "Construction", "Low-carbon concrete formulation with industrial by-products."),
    ("OceanData", "Marine", "Underwater acoustic sensor networks for coastal monitoring."),
    ("FuelCellOne", "Energy", "PEM fuel cell stack design and balance-of-plant optimization."),
    ("RoboArm Industries", "Robotics", "Collaborative robot arm for assembly tasks with vision-guided pick-and-place."),
    ("Telemed Connect", "Healthtech", "Tele-ICU platform with AI-assisted patient deterioration alerts."),
    ("MicroGrid Tech", "Energy", "Distributed energy resource management for rural microgrids."),
    ("ChipDesign Lab", "Semiconductor", "Custom AI accelerator ASIC design for edge inference."),
]

REJECT_REASONS = [
    "expertise mismatch",
    "professor unavailable",
    "budget constraints",
    "timeline conflict",
    "found better fit externally",
    "research focus too theoretical",
    "geography mismatch",
    "scope too broad",
]


def acceptance_probability(score: float) -> float:
    if score >= 80:
        return 0.75
    if score >= 60:
        return 0.50
    return 0.25


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path(__file__).parent / "collabv_data.db"))
    parser.add_argument("--reset", action="store_true", help="Wipe existing feedback before seeding")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)

    db.DB_PATH = args.db
    db.init_db(args.db)
    if args.reset:
        conn = sqlite3.connect(args.db)
        conn.execute("DELETE FROM feedback")
        conn.execute("DELETE FROM match_results")
        conn.execute("DELETE FROM company_requests")
        conn.commit()
        conn.close()
        print("Reset existing feedback + match data")

    print("Loading matching engine...")
    engine = MatchingEngine(enable_embeddings=False)
    print(f"Engine ready: {len(engine.professors)} professors\n")

    feedback_records = []
    total_matches_seeded = 0

    for i, (name, industry, brief) in enumerate(COMPANY_BRIEFS, start=1):
        cid = f"SEED-{uuid.uuid4().hex[:8].upper()}"
        request = CompanyRequest(
            company_id=cid,
            company_name=name,
            technical_area=[],
            required_expertise=[],
            tech_stack=[],
            industry=industry,
            project_description=brief,
            challenges="",
            collaboration_type="Joint Research",
            location_preference="Any",
            research_level="applied",
            budget_tier="medium",
            timeline_months=12,
        )
        db.save_request(cid, {
            "company_name": name, "industry": industry,
            "project_description": brief, "raw_text": brief,
        })

        matches = engine.match(request, top_k=5)
        match_id = f"M-{uuid.uuid4().hex[:8].upper()}"
        results_payload = [
            {
                "professor_id": m.professor_id,
                "professor_name": m.professor_name,
                "department": m.department,
                "score": m.score,
                "tier1_score": m.tier1_score,
                "tier2_score": m.tier2_score,
                "tier3_score": m.tier3_score,
                "patent_score": m.patent_score,
                "readiness_score": m.readiness_score,
                "contextual_readiness": m.contextual_readiness,
                "reasons": m.reasons,
            }
            for m in matches
        ]
        db.save_result(match_id, cid, name, results_payload, None)
        total_matches_seeded += len(matches)

        for m in matches:
            p_accept = acceptance_probability(m.score)
            if rng.random() < p_accept:
                action = "accept"
                reason = ""
            else:
                action = "reject"
                reason = rng.choice(REJECT_REASONS)
            db.save_feedback(match_id, m.professor_id, action, reason)
            feedback_records.append({
                "match_id": match_id,
                "professor_id": m.professor_id,
                "score": m.score,
                "action": action,
                "reason": reason,
            })
        if i % 5 == 0:
            print(f"  [{i:2d}/30] seeded - cumulative feedback: {len(feedback_records)}")

    # Stats
    accepts = sum(1 for f in feedback_records if f["action"] == "accept")
    rejects = len(feedback_records) - accepts
    print()
    print(f"Total feedback seeded : {len(feedback_records)}")
    print(f"  Accepts             : {accepts}  ({accepts/len(feedback_records)*100:.0f}%)")
    print(f"  Rejects             : {rejects}  ({rejects/len(feedback_records)*100:.0f}%)")
    print(f"Match results saved   : {total_matches_seeded}")

    # Reason distribution
    reason_counts = {}
    for f in feedback_records:
        if f["reason"]:
            reason_counts[f["reason"]] = reason_counts.get(f["reason"], 0) + 1
    if reason_counts:
        print("\nReject-reason distribution:")
        for r, c in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"  {r:30s}  {c}")


if __name__ == "__main__":
    main()
