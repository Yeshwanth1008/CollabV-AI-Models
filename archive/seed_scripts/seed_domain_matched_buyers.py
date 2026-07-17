"""Seed a second batch of synthetic buyers that mirror the actual patent-domain
distribution of the 898 listings (heavily hardware / materials / chemistry /
biotech / energy / sensors), not the AI/ML/IoT skew of the original 100.

Two distinct synthetic batches now coexist in buyer_profiles:
  1. The original 100 from 100_Companies_Collaboration_Schema.xlsx
     (email pattern: synthetic-<uuid>@collabv.local)
  2. This batch — domain-matched to listing inventory
     (email pattern: domain-buyer-<slug>@collabv.local)

Both carry is_synthetic=True so they're excluded from real inventor rankings
unless include_synthetic=True is set by an admin.

Idempotent: deletes-and-reseeds rows in batch (2) only, by email prefix. Does
NOT touch batch (1) — that script handles its own row management.

Vocabulary alignment: technical_areas and use_cases are written using terms
that live in innovation_scorer._DOMAIN_KEYWORDS so the reranker's
domain_overlap feature actually fires for this buyer pool. The reason the
prior Mode A cosines came back at 0.07–0.11 wasn't bad embeddings — it was
that no buyer's text contained vocab a hardware patent's text could match.
"""
from __future__ import annotations

import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import collabv.auth as auth                  # noqa: E402
import collabv.marketplace_db as mdb         # noqa: E402

DB_PATH = str(ROOT / "collabv_data.db")
EMAIL_PREFIX = "domain-buyer-"  # used to identify rows for delete-and-reseed
USER_ID_PREFIX = "USR-DBSY-"    # deterministic so reseed is stable

# ─── Buyer specs ──────────────────────────────────────────────────────────
# Each entry maps to one synthetic buyer. Composition (~36 buyers) reflects
# the per-domain listing distribution I just profiled:
#   sensors_iot 15% materials 9.5% chemicals 8% energy 5.7% biotech 5.6%
#   fluid_thermal 5% electronics 4% ai_ml 4% optics 4% robotics 2.6%
#   healthcare 2.4% civil/manufacturing ~2% aerospace 0.2%
#
# Original 100 buyers already cover ai_ml/robotics/electronics densely.
# We bias this batch HEAVY toward what they don't cover:
#   materials, chemicals, biotech, energy, fluid_thermal, healthcare,
#   sensors-as-hardware (not IoT/AI sensing), optics, civil, manufacturing,
#   aerospace.

BUYERS: List[Dict[str, Any]] = [
    # ── materials (4) ────────────────────────────────────────────────────
    {"slug": "novalloy",  "org_name": "NovAlloy Metals",
     "industry": "Advanced Materials & Metallurgy", "org_type": "enterprise",
     "industries_of_interest": ["materials", "manufacturing"],
     "technical_areas": ["alloy", "composite", "metallurgy", "ceramic"],
     "use_cases":
        "Industrial supplier of magnesium and aluminum alloys for automotive and "
        "aerospace. Looking to license processes for high-performance alloy "
        "castings, polymer-metal composites, and surface-coating technologies "
        "that improve fatigue life and corrosion resistance.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "polynexus", "org_name": "PolyNexus Composites",
     "industry": "Polymer Engineering", "org_type": "enterprise",
     "industries_of_interest": ["materials", "chemicals"],
     "technical_areas": ["polymer", "composite", "nanomaterial", "ceramic"],
     "use_cases":
        "Sourcing IP around polymer composites and nanomaterial reinforcements "
        "for structural applications. Particular interest in biodegradable and "
        "stimuli-responsive polymers for packaging and medical devices.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    {"slug": "carbonworks", "org_name": "CarbonWorks Industries",
     "industry": "Carbon Materials", "org_type": "enterprise",
     "industries_of_interest": ["materials", "energy"],
     "technical_areas": ["nanomaterial", "composite", "materials", "alloy"],
     "use_cases":
        "Manufacturer of graphene and carbon-nanotube based composites for "
        "battery electrodes and high-strength structural materials. Looking to "
        "license catalyst-grown nanostructures, doped graphene, and graphenic "
        "fiber preparation methods.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "ferromaterials", "org_name": "FerroMaterials Pvt Ltd",
     "industry": "Magnetic Materials", "org_type": "startup",
     "industries_of_interest": ["materials", "electronics"],
     "technical_areas": ["nanomaterial", "alloy", "materials", "ceramic"],
     "use_cases":
        "Develops soft magnetic materials and ferromagnetic nanostructures for "
        "sensor and energy-storage applications. Evaluating processes for "
        "hetero-atom-induced ferromagnetism and atomic hydrogenated magnetic "
        "nanomaterials.",
     "budget_band": "medium", "tech_maturity_preference": "early_stage"},

    # ── chemicals (4) ────────────────────────────────────────────────────
    {"slug": "catalyticsynth", "org_name": "Catalytic Synth India",
     "industry": "Specialty Chemicals", "org_type": "enterprise",
     "industries_of_interest": ["chemicals", "manufacturing"],
     "technical_areas": ["catalyst", "polymerization", "reaction", "membrane"],
     "use_cases":
        "Specialty-chemicals manufacturer scaling catalytic processes for fine "
        "chemicals and pharma intermediates. Interested in licensing novel "
        "catalysts for hydrogen evolution, electrocatalysts for fuel cells, "
        "and selective separation membranes for downstream processing.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "sephura", "org_name": "Sephura Separations",
     "industry": "Industrial Filtration & Separation", "org_type": "enterprise",
     "industries_of_interest": ["chemicals", "materials"],
     "technical_areas": ["membrane", "separation", "polymer", "catalyst"],
     "use_cases":
        "Membrane and adsorbent media for industrial separation: water "
        "purification, removal of arsenic and fluoride from drinking water, "
        "and pesticide and dye removal from waste-water effluents. Looking "
        "for graphenic, cellulose, and metal-organic-framework adsorbents.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    {"slug": "aquaclear", "org_name": "AquaClear Water Solutions",
     "industry": "Water Treatment & Purification", "org_type": "enterprise",
     "industries_of_interest": ["chemicals", "materials"],
     "technical_areas": ["membrane", "separation", "nanomaterial", "polymer"],
     "use_cases":
        "Point-of-use drinking-water purifier company. Interested in licensing "
        "silver-nanoparticle antimicrobial compositions, graphene-iron-oxide "
        "adsorbents for arsenic removal, polyaniline-cellulose fluoride "
        "removers, and capacitive desalination electrodes.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    {"slug": "nanoreactions", "org_name": "NanoReactions Lab",
     "industry": "Nanochemistry & Synthesis", "org_type": "startup",
     "industries_of_interest": ["chemicals", "biotech"],
     "technical_areas": ["reaction", "catalyst", "nanomaterial", "polymerization"],
     "use_cases":
        "Contract-research startup synthesizing atomically precise metal "
        "nanoclusters and quantum dots for sensing and biomedical use. "
        "Sourcing IP on monolayer-protected nanoclusters, microdroplet "
        "synthesis of nanoparticles, and electrospray deposition processes.",
     "budget_band": "low", "tech_maturity_preference": "early_stage"},

    # ── biotech (4) ──────────────────────────────────────────────────────
    {"slug": "bioscaffold", "org_name": "BioScaffold Therapeutics",
     "industry": "Regenerative Medicine", "org_type": "startup",
     "industries_of_interest": ["biotech", "healthcare"],
     "technical_areas": ["biopolymer", "protein", "biomarker", "enzyme"],
     "use_cases":
        "Developing 3D-printed polymer scaffolds for periodontal and bone "
        "regeneration. Looking for IP on biodegradable scaffold compositions, "
        "cell-alignment substrates, and luminescent protein-protected metal "
        "clusters for diagnostic imaging.",
     "budget_band": "medium", "tech_maturity_preference": "early_stage"},

    {"slug": "drugdelivery", "org_name": "TargetDose Pharma",
     "industry": "Drug Delivery Systems", "org_type": "enterprise",
     "industries_of_interest": ["biotech", "healthcare"],
     "technical_areas": ["drug", "biopolymer", "protein", "biomarker"],
     "use_cases":
        "Develops carrier systems for targeted oncology drug delivery. "
        "Evaluating spacerless carbon nanotubes for cancer drug carriers, "
        "graphite-oxide chemo platforms, and antibody-conjugated nanocarriers. "
        "Will license proven preclinical-stage compositions.",
     "budget_band": "high", "tech_maturity_preference": "mid_stage"},

    {"slug": "enzymeworks", "org_name": "EnzymeWorks Bio",
     "industry": "Industrial Biotechnology", "org_type": "startup",
     "industries_of_interest": ["biotech", "chemicals"],
     "technical_areas": ["enzyme", "protein", "biopolymer", "genom"],
     "use_cases":
        "Engineered enzymes for industrial biocatalysis: chiral synthesis, "
        "starch hydrolysis, biopolymer production. Sourcing IP on engineered "
        "protein scaffolds, immobilization matrices, and high-throughput "
        "expression systems.",
     "budget_band": "low", "tech_maturity_preference": "mid_stage"},

    {"slug": "diagnoslab", "org_name": "DiagnosLab Solutions",
     "industry": "Molecular Diagnostics", "org_type": "enterprise",
     "industries_of_interest": ["biotech", "healthcare"],
     "technical_areas": ["biomarker", "diagnostic", "protein", "enzyme"],
     "use_cases":
        "Diagnostics company building point-of-care biomarker assays. "
        "Interested in multimodal diagnostic probes, paper-spray mass "
        "spectrometry for analyte detection, and luminescence-based detection "
        "platforms for arsenic and pathogens.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    # ── energy / batteries / fuel cells (4) ─────────────────────────────
    {"slug": "voltcore", "org_name": "VoltCore Energy Systems",
     "industry": "Battery & Energy Storage", "org_type": "enterprise",
     "industries_of_interest": ["energy", "materials"],
     "technical_areas": ["battery", "energy storage", "fuel cell", "hydrogen"],
     "use_cases":
        "Develops lithium-ion and sodium-ion battery systems for grid storage. "
        "Looking for IP on long-cycle-life anode materials, solid electrolyte "
        "membranes, iron-ion rechargeable chemistries, and improved separator "
        "electrode assemblies.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "h2green", "org_name": "H2Green Industries",
     "industry": "Hydrogen Economy", "org_type": "enterprise",
     "industries_of_interest": ["energy", "chemicals"],
     "technical_areas": ["hydrogen", "fuel cell", "catalyst", "energy storage"],
     "use_cases":
        "Green hydrogen producer scaling water electrolysis and storage. "
        "Sourcing electrolyzer catalysts, hydrogen storage nanomaterials, "
        "non-precious electrocatalysts for proton-exchange-membrane fuel "
        "cells, and graphene-based hydrogen storage materials.",
     "budget_band": "high", "tech_maturity_preference": "mid_stage"},

    {"slug": "solarivolt", "org_name": "Solarivolt Renewables",
     "industry": "Photovoltaics & Solar", "org_type": "enterprise",
     "industries_of_interest": ["energy", "electronics"],
     "technical_areas": ["solar", "photovoltaic", "energy storage", "grid"],
     "use_cases":
        "Solar-panel and BoS supplier. Evaluating IP on perovskite cells, "
        "anti-soiling coatings, MPPT power-electronics topologies, and "
        "battery-hybrid grid-tied storage. Looking for proven-stage tech.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "gridflex", "org_name": "GridFlex Power Electronics",
     "industry": "Power Electronics", "org_type": "startup",
     "industries_of_interest": ["energy", "electronics"],
     "technical_areas": ["power electronics", "semiconductor", "grid", "battery"],
     "use_cases":
        "Power-electronics startup focused on EV chargers and grid converters. "
        "Sourcing IP on voltage-stress-control circuits, multi-level converter "
        "topologies, and wide-bandgap semiconductor switching strategies.",
     "budget_band": "medium", "tech_maturity_preference": "early_stage"},

    # ── fluid_thermal / engines (3) ─────────────────────────────────────
    {"slug": "thermaflo", "org_name": "ThermaFlo Engineering",
     "industry": "Thermal Management Systems", "org_type": "enterprise",
     "industries_of_interest": ["fluid_thermal", "manufacturing"],
     "technical_areas": ["thermal", "heat transfer", "fluid", "cfd"],
     "use_cases":
        "Industrial heat-exchanger manufacturer. Evaluating IP on enhanced "
        "surface condensation, micro-channel heat transfer, and thermal-"
        "humidity management devices for data centers and industrial cooling.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    {"slug": "combustek", "org_name": "Combustek Engines",
     "industry": "Internal Combustion Systems", "org_type": "enterprise",
     "industries_of_interest": ["fluid_thermal", "manufacturing"],
     "technical_areas": ["combustion", "fluid", "thermal", "turbomachinery"],
     "use_cases":
        "Engine OEM for off-highway and marine. Sourcing IP on variable valve "
        "duration mechanisms, dynamic fuel-blending systems for IC engines, "
        "low-emission combustion chamber designs, and turbocharger improvements.",
     "budget_band": "high", "tech_maturity_preference": "mid_stage"},

    {"slug": "cfdworks", "org_name": "CFDworks Simulations",
     "industry": "Computational Fluid Dynamics", "org_type": "startup",
     "industries_of_interest": ["fluid_thermal", "aerospace"],
     "technical_areas": ["cfd", "fluid", "aerodynamic", "turbomachinery"],
     "use_cases":
        "CFD-services startup for turbomachinery and aerospace clients. "
        "Looking to license validated solver methods, turbulence closures for "
        "compressible flows, and reduced-order models for aerodynamic design.",
     "budget_band": "low", "tech_maturity_preference": "mid_stage"},

    # ── sensors (hardware-side, 4) ──────────────────────────────────────
    {"slug": "sensekit", "org_name": "SenseKit Hardware",
     "industry": "Sensor Hardware", "org_type": "enterprise",
     "industries_of_interest": ["sensors_iot", "electronics"],
     "technical_areas": ["sensor", "biomedical device", "wearable"],
     "use_cases":
        "Builds rugged industrial and biomedical sensor hardware. Interested "
        "in colorimetric detection devices, paper-spray ionization sensors, "
        "strain gauges, and flexible humidity / chlorine / arsenic detectors.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    {"slug": "biosignal", "org_name": "BioSignal Devices",
     "industry": "Wearable Biomedical Devices", "org_type": "startup",
     "industries_of_interest": ["sensors_iot", "healthcare"],
     "technical_areas": ["wearable", "biomedical device", "sensor"],
     "use_cases":
        "Wearable cardiac and respiratory monitor startup. Sourcing IP on "
        "magnetic transducer pulse-detection systems, flexible strain sensors, "
        "non-invasive blood-flow sensors, and breath-humidity sensor "
        "fabrication.",
     "budget_band": "low", "tech_maturity_preference": "early_stage"},

    {"slug": "chemsense", "org_name": "ChemSense Analytics",
     "industry": "Chemical Sensing Instruments", "org_type": "enterprise",
     "industries_of_interest": ["sensors_iot", "chemicals"],
     "technical_areas": ["sensor", "biomedical device"],
     "use_cases":
        "Analytical-instruments maker. Evaluating IP on superhydrophobic paper-"
        "spray mass-spectrometry preconcentration, fluoride-specific "
        "colorimetric sensors integrated with smartphones, and trace-metal "
        "amperometric detection electrodes.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    {"slug": "iiotsense", "org_name": "IIoTsense Systems",
     "industry": "Industrial IoT", "org_type": "enterprise",
     "industries_of_interest": ["sensors_iot", "manufacturing"],
     "technical_areas": ["sensor", "iot", "wearable"],
     "use_cases":
        "Industrial-IoT systems integrator for plant monitoring. Looking for "
        "IP on edge sensor nodes with rugged packaging, low-power MEMS "
        "sensors, and condition-monitoring strain and humidity sensors for "
        "asset-heavy industries.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    # ── optics / photonics (3) ─────────────────────────────────────────
    {"slug": "lumenlogic", "org_name": "LumenLogic Photonics",
     "industry": "Photonics & Lasers", "org_type": "enterprise",
     "industries_of_interest": ["optics", "electronics"],
     "technical_areas": ["optical", "photonic", "laser", "fiber optic"],
     "use_cases":
        "Photonics manufacturer of fiber-optic components and laser modules. "
        "Sourcing IP on plasmonic Raman scattering microspectroscopy, optical "
        "switching thin-film electrochromics, and integrated photonic "
        "interferometer designs.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "spectraco", "org_name": "Spectra Co Imaging",
     "industry": "Optical Imaging Systems", "org_type": "startup",
     "industries_of_interest": ["optics", "healthcare"],
     "technical_areas": ["optical", "photonic", "laser"],
     "use_cases":
        "Builds custom optical-imaging systems for biomedical and industrial "
        "inspection. Looking to license plasmonic colocalization techniques, "
        "Raman spectroscopy enhancements, and luminescence-based metal-"
        "cluster detection systems.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    {"slug": "fiberlink", "org_name": "FiberLink Networks",
     "industry": "Optical Networking", "org_type": "enterprise",
     "industries_of_interest": ["optics", "electronics"],
     "technical_areas": ["fiber optic", "optical", "photonic"],
     "use_cases":
        "Telecom-grade fiber-optic networking equipment supplier. Evaluating "
        "IP on low-loss waveguide structures, photonic switching components, "
        "and integrated optical sensors for network monitoring.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    # ── healthcare (3) ─────────────────────────────────────────────────
    {"slug": "rehabtech", "org_name": "RehabTech Devices",
     "industry": "Rehabilitation Devices", "org_type": "enterprise",
     "industries_of_interest": ["healthcare", "sensors_iot"],
     "technical_areas": ["rehabilitation", "prosthet", "medical", "implant"],
     "use_cases":
        "Rehabilitation device OEM. Interested in IP on battery-powered sit-"
        "to-stand devices for paraplegic patients, biomechanical electrolarynx "
        "voice-rehabilitation devices, and prosthetic mobility aids.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    {"slug": "surgicalflow", "org_name": "SurgicalFlow Instruments",
     "industry": "Surgical & Diagnostic Devices", "org_type": "enterprise",
     "industries_of_interest": ["healthcare", "biotech"],
     "technical_areas": ["surgical", "medical", "diagnostic", "implant"],
     "use_cases":
        "Surgical-instrument manufacturer. Sourcing IP on multimodal "
        "diagnostic probes, novel implant compositions, and antibacterial "
        "silver-cluster coatings for medical devices.",
     "budget_band": "high", "tech_maturity_preference": "mid_stage"},

    {"slug": "cardiocare", "org_name": "CardioCare Medical",
     "industry": "Cardiovascular Devices", "org_type": "startup",
     "industries_of_interest": ["healthcare", "sensors_iot"],
     "technical_areas": ["medical", "diagnostic", "biomedical device"],
     "use_cases":
        "Cardiovascular diagnostics startup. Looking for IP on non-invasive "
        "blood-flow pulse detection, magnetic-transducer cardiac monitoring "
        "systems, and wearable arrhythmia detection devices.",
     "budget_band": "low", "tech_maturity_preference": "early_stage"},

    # ── civil / construction (2) ───────────────────────────────────────
    {"slug": "structuracon", "org_name": "Structura Construction",
     "industry": "Civil Engineering & Construction", "org_type": "enterprise",
     "industries_of_interest": ["civil", "materials"],
     "technical_areas": ["structural", "concrete", "construction", "seismic"],
     "use_cases":
        "Construction firm scaling pre-engineered structural systems. "
        "Sourcing IP on 3D-printed waffle slabs, high-performance concrete "
        "compositions, and seismic-resistant structural joint designs.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    {"slug": "geotechind", "org_name": "Geotech Indus India",
     "industry": "Geotechnical Engineering", "org_type": "enterprise",
     "industries_of_interest": ["civil", "materials"],
     "technical_areas": ["geotechnical", "structural", "transportation"],
     "use_cases":
        "Geotechnical contractor for transportation infrastructure. Evaluating "
        "IP on soil-stabilization composites, geosynthetic reinforcement, and "
        "instrumented foundation monitoring for highway projects.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    # ── manufacturing (2) ──────────────────────────────────────────────
    {"slug": "additivemfg", "org_name": "Additive Manufacturing Co",
     "industry": "Additive Manufacturing", "org_type": "enterprise",
     "industries_of_interest": ["manufacturing", "materials"],
     "technical_areas": ["additive manufacturing", "manufacturing", "machining"],
     "use_cases":
        "Industrial 3D-printing services for metal and polymer parts. "
        "Sourcing IP on multi-axial forming apparatus, novel additive-"
        "manufacturing alloy feedstocks, and post-processing surface "
        "treatments.",
     "budget_band": "medium", "tech_maturity_preference": "proven"},

    {"slug": "machineworks", "org_name": "MachineWorks Precision",
     "industry": "Precision Machining", "org_type": "enterprise",
     "industries_of_interest": ["manufacturing", "fluid_thermal"],
     "technical_areas": ["machining", "manufacturing", "additive manufacturing"],
     "use_cases":
        "Precision-machining shop serving aerospace and medical clients. "
        "Looking for IP on high-temperature multiaxial formability methods, "
        "tooling lifetime extension coatings, and process monitoring "
        "instrumentation.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},

    # ── aerospace (1) ──────────────────────────────────────────────────
    {"slug": "skyframe", "org_name": "SkyFrame Aerospace",
     "industry": "Aerospace Systems", "org_type": "enterprise",
     "industries_of_interest": ["aerospace", "materials"],
     "technical_areas": ["aerospace", "aircraft", "propulsion", "aerodynamic"],
     "use_cases":
        "Aerospace structures and propulsion. Sourcing IP on lightweight "
        "alloys, composite airframe layups, propulsion-thermal management, "
        "and UAV aerodynamic control surfaces.",
     "budget_band": "high", "tech_maturity_preference": "proven"},

    # ── robotics (1) ───────────────────────────────────────────────────
    {"slug": "armworks", "org_name": "ArmWorks Robotics",
     "industry": "Industrial Robotics", "org_type": "enterprise",
     "industries_of_interest": ["robotics", "manufacturing"],
     "technical_areas": ["robotic", "manipulator", "gripper", "autonomous"],
     "use_cases":
        "Industrial-robotics integrator focused on welding and assembly. "
        "Evaluating IP on novel gripper designs, compliant manipulator "
        "joints, and humanoid manipulator control schemes.",
     "budget_band": "medium", "tech_maturity_preference": "mid_stage"},
]


def _purge_previous_batch(conn: sqlite3.Connection) -> int:
    """Delete the previous run's rows (matched by EMAIL_PREFIX). Returns count."""
    # Resolve user_ids first so we can also delete the buyer_profiles rows
    user_rows = conn.execute(
        "SELECT id FROM users WHERE email LIKE ?", (f"{EMAIL_PREFIX}%@collabv.local",),
    ).fetchall()
    user_ids = [r[0] for r in user_rows]
    if not user_ids:
        return 0
    placeholders = ",".join("?" for _ in user_ids)
    conn.execute(f"DELETE FROM buyer_profiles WHERE user_id IN ({placeholders})", user_ids)
    conn.execute(f"DELETE FROM users WHERE id IN ({placeholders})", user_ids)
    conn.commit()
    return len(user_ids)


def main():
    mdb.init_marketplace_tables(DB_PATH)
    auth.init_auth_tables(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    try:
        purged = _purge_previous_batch(conn)
        print(f"Purged {purged} rows from previous batch.")

        now = time.time()
        for i, b in enumerate(BUYERS):
            slug = b["slug"]
            user_id = f"{USER_ID_PREFIX}{slug[:10].upper()}"
            email = f"{EMAIL_PREFIX}{slug}@collabv.local"
            # Create a corresponding user row (buyer_user role)
            conn.execute(
                """INSERT INTO users
                   (id, email, password_hash, name, company_name, role,
                    api_key, tier, created_at, linked_professor_id)
                   VALUES (?, ?, ?, ?, ?, 'buyer_user', ?, 'free', ?, NULL)""",
                (user_id, email, "x" * 60, b["org_name"], b["org_name"],
                 f"key-{uuid.uuid4().hex}", now),
            )
            conn.commit()
            # Buyer profile row goes through save_buyer
            mdb.save_buyer({
                "user_id": user_id,
                "org_name": b["org_name"],
                "org_type": b["org_type"],
                "industry": b["industry"],
                "industries_of_interest": b["industries_of_interest"],
                "technical_areas": b["technical_areas"],
                "use_cases": b["use_cases"],
                "tech_maturity_preference": b["tech_maturity_preference"],
                "budget_band": b["budget_band"],
                "geographic_scope": ["India"],
                "seller_preferences": {},
                "is_synthetic": True,
            }, db_path=DB_PATH)
        print(f"Seeded {len(BUYERS)} domain-matched synthetic buyers.")
        n_total = conn.execute(
            "SELECT COUNT(*) FROM buyer_profiles WHERE is_synthetic=1"
        ).fetchone()[0]
        n_batch = conn.execute(
            "SELECT COUNT(*) FROM users WHERE email LIKE ?",
            (f"{EMAIL_PREFIX}%@collabv.local",),
        ).fetchone()[0]
        print(f"Total synthetic buyers in DB: {n_total} ({n_batch} from this batch)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
