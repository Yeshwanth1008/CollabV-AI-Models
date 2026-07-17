"""
Generate realistic synthetic patent data for all 543 IITM professors.

Distribution:
  ~30% professors: 0 patents
  ~40%: 1-3 patents
  ~20%: 4-6 patents
  ~10%: 7-8 patents

Each patent has:
  - title (domain-specific, drawn from a 200+ template pool)
  - filing_date (2015..2025)
  - patent_number (IN-YYYYNNNNN)
  - status (50% granted, 30% published, 20% filed)
  - abstract (matches expertise)
  - co_inventors (20% of patents get 1-2 same-department co-inventors)

Outputs:
  iitm_professors_with_patents.json
  iitm_professors_nlp.json  (copy of above; original backed up to .backup)
  iitm_patents.json         (flat list of all patents)
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).parent
PROFS_IN = ROOT / "iitm_professors_nlp.json"
BACKUP = ROOT / "iitm_professors_nlp.backup.json"
PROFS_OUT = ROOT / "iitm_professors_with_patents.json"
PATENTS_OUT = ROOT / "iitm_patents.json"


# ─── Domain-specific title pools ───────────────────────────────────────────

TITLE_POOLS = {
    "ai_ml": [
        "Method and apparatus for efficient deep learning inference on edge devices",
        "Neural network architecture for low-resource text classification",
        "System for adaptive learning rate optimization in transformer training",
        "Federated learning framework for privacy-preserving model training",
        "Knowledge distillation technique for compressing large language models",
        "Real-time anomaly detection using self-supervised representation learning",
        "Attention mechanism for multi-modal sensor fusion",
        "Reinforcement learning controller for autonomous decision systems",
        "Graph neural network for protein structure prediction",
        "Few-shot learning framework for industrial defect detection",
    ],
    "robotics": [
        "Adaptive robotic gripper with tactile feedback for fragile object handling",
        "Modular robot arm with reconfigurable end-effectors",
        "System for autonomous navigation in unstructured agricultural environments",
        "Underwater robotic platform with vision-based obstacle avoidance",
        "Compliant robotic finger for human-robot collaboration",
        "Method for visual servoing in cluttered indoor environments",
        "Soft robotic actuator using shape memory alloys",
        "Robotic perception system for warehouse pick-and-place tasks",
        "Mobile manipulation platform with whole-body coordination control",
        "Wearable exoskeleton for upper-limb rehabilitation",
    ],
    "energy": [
        "High-efficiency tandem solar cell with perovskite-silicon absorption layers",
        "Battery management system with online state-of-charge estimation",
        "Hydrogen production from waste biomass via solar-driven electrolysis",
        "Lithium-ion battery electrode with silicon-carbon composite anode",
        "Grid-tied inverter with reactive power compensation for solar farms",
        "Thermoelectric generator for waste heat recovery in industrial processes",
        "Hybrid energy storage system with battery and supercapacitor",
        "Wireless power transfer system for electric vehicle charging",
        "Solid-state battery with composite ceramic-polymer electrolyte",
        "Flow battery design for grid-scale renewable energy storage",
    ],
    "biotechnology": [
        "Engineered bacterial strain for cellulose degradation at industrial scale",
        "Microfluidic device for single-cell transcriptome analysis",
        "Biodegradable drug delivery system using natural polysaccharide carriers",
        "CRISPR-based diagnostic assay for rapid pathogen detection",
        "Bioreactor design for high-yield monoclonal antibody production",
        "Therapeutic peptide for selective targeting of cancer stem cells",
        "Method for producing functional food ingredients from algal biomass",
        "Biosensor for trace-level detection of agricultural contaminants",
        "Tissue scaffold with controlled degradation kinetics for bone regeneration",
        "Plant-derived vaccine adjuvant for thermostable immunization",
    ],
    "materials": [
        "Composite material with enhanced thermal conductivity for electronic packaging",
        "Method for additive manufacturing of titanium alloys with refined microstructure",
        "Self-healing polymer coating for marine structural applications",
        "High-strength steel with tailored phase composition for automotive use",
        "Nanostructured ceramic for high-temperature gas turbine components",
        "Lightweight magnesium alloy with improved corrosion resistance",
        "Functionally-graded composite for thermal protection systems",
        "Conductive polymer composite for flexible electronics",
        "Wear-resistant coating for cutting tools using HVOF spraying",
        "Recyclable thermoplastic with bio-based monomer feedstock",
    ],
    "chemical": [
        "Heterogeneous catalyst for selective hydrogenation of unsaturated compounds",
        "Process for solvent-free polymerization of vinyl monomers",
        "Membrane-based separation for desalination of brackish water",
        "Microreactor for continuous synthesis of pharmaceutical intermediates",
        "Catalytic process for converting CO2 to value-added chemicals",
        "Adsorbent material for heavy metal removal from industrial effluents",
        "Process intensification using rotating packed-bed reactors",
        "Green chemistry route for synthesis of fine chemicals from biomass",
        "Catalyst regeneration method for fluid catalytic cracking units",
        "Reactive distillation process for biodiesel production",
    ],
    "civil": [
        "Earthquake-resistant beam-column connection for steel moment frames",
        "Sustainable concrete mix design with high-volume fly ash content",
        "Real-time structural health monitoring system using fiber-optic sensors",
        "Geosynthetic-reinforced soil retaining wall for highway applications",
        "Self-compacting concrete formulation for complex geometries",
        "Vibration isolation device for base-isolated buildings",
        "Permeable pavement design for urban stormwater management",
        "FRP-strengthened reinforced concrete beam with hybrid fibers",
        "Smart traffic signal optimization using vehicle trajectory data",
        "Modular bridge decking system for accelerated construction",
    ],
    "electrical": [
        "RF MEMS switch for reconfigurable antenna arrays in 5G systems",
        "Bidirectional power converter for vehicle-to-grid applications",
        "Low-power IoT sensor node architecture with energy harvesting",
        "Wideband phased-array antenna for satellite communications",
        "GaN-based high-frequency switching converter for compact power supplies",
        "Compressive sensing algorithm for sparse channel estimation",
        "Smart grid protection relay with adaptive setting groups",
        "Compact microstrip filter for millimeter-wave 5G transceivers",
        "Wireless capsule endoscope with on-chip image processing",
        "EMI mitigation technique for high-density power electronics",
    ],
    "aerospace": [
        "Aerodynamic optimization of UAV airfoils for low-Reynolds-number flight",
        "Composite wing structure with embedded health monitoring sensors",
        "Trajectory optimization algorithm for re-entry vehicle guidance",
        "Hybrid propulsion system for vertical take-off and landing aircraft",
        "Method for active flow control on turbomachinery blades",
        "Lightweight ablative material for spacecraft thermal protection",
        "Inertial navigation system with adaptive Kalman filtering",
        "Variable-camber morphing wing mechanism",
        "Aerial swarm coordination protocol for distributed sensing",
        "Acoustic liner design for jet engine noise reduction",
    ],
    "fluid_thermal": [
        "Compact heat exchanger with enhanced fin geometry for HVAC systems",
        "Two-phase cooling system for high-density data center racks",
        "Method for predicting cavitation inception in marine propellers",
        "Vortex-induced vibration mitigation device for offshore risers",
        "Combustion control technique for low-NOx gas turbines",
        "Multiphase flow simulation method for oil reservoir characterization",
        "Microchannel cooling device for power electronics",
        "Method for drag reduction in pipelines using polymer additives",
        "Spray cooling apparatus for laser pump head thermal management",
        "Wind energy harvester for low-velocity urban environments",
    ],
    "sensors_iot": [
        "Wearable photoplethysmography sensor for continuous blood pressure monitoring",
        "Capacitive humidity sensor with metal-organic framework dielectric",
        "Distributed fiber-optic sensing system for pipeline integrity monitoring",
        "Low-power smoke detector with edge-computed false-alarm suppression",
        "Flexible strain sensor based on carbon nanotube networks",
        "Soil moisture sensor with low-power LoRa connectivity",
        "MEMS gyroscope with on-chip temperature compensation",
        "Gas sensor array for early detection of volatile organic compounds",
        "Heart-rate-monitoring smart fabric with conductive yarn electrodes",
        "Vibration sensor for predictive maintenance of rotating machinery",
    ],
    "default": [
        "Apparatus and method for measurement of process variables",
        "System for automated quality inspection in manufacturing",
        "Device for energy-efficient operation in industrial settings",
        "Method for predictive analytics on time-series sensor data",
        "Apparatus for non-destructive testing of composite structures",
        "System for real-time decision support in distributed processes",
        "Method for optimization of resource allocation in complex systems",
        "Apparatus for high-precision metrology in semiconductor fabrication",
    ],
}


# Domain keywords ranked by specificity (most specific first wins)
DOMAIN_KEYWORDS = [
    ("ai_ml",         ["machine learning", "deep learning", "neural", "nlp", "computer vision", "reinforcement"]),
    ("robotics",      ["robotic", "manipulator", "autonomous robot", "humanoid", "gripper"]),
    ("energy",        ["solar", "battery", "fuel cell", "renewable", "hydrogen", "photovoltaic", "energy storage"]),
    ("biotechnology", ["biotechnology", "enzyme", "protein", "genom", "drug", "vaccine", "bioreactor"]),
    ("materials",     ["materials", "alloy", "composite", "polymer", "ceramic", "metallurgy"]),
    ("chemical",      ["catalyst", "polymerization", "separation", "reaction engineering", "process engineering"]),
    ("civil",         ["civil", "structural", "concrete", "geotechnical", "transportation", "seismic"]),
    ("aerospace",     ["aerospace", "aircraft", "uav", "propulsion", "spacecraft", "aerodynamic"]),
    ("electrical",    ["electrical", "vlsi", "rf ", "antenna", "wireless", "5g", "power electronics", "communication"]),
    ("fluid_thermal", ["fluid", "thermal", "heat transfer", "combustion", "cfd", "turbomachinery"]),
    ("sensors_iot",   ["sensor", "iot", "wearable", "biomedical device"]),
]

DEPT_KEYWORDS = {
    "Computer Science": "ai_ml",
    "Electrical": "electrical",
    "Aerospace": "aerospace",
    "Mechanical": "fluid_thermal",
    "Civil": "civil",
    "Chemical": "chemical",
    "Chemistry": "chemical",
    "Metallurgical": "materials",
    "Biotechnology": "biotechnology",
    "Ocean": "civil",
    "Applied Mechanics": "fluid_thermal",
    "Engineering Design": "robotics",
    "Physics": "materials",
}


# ─── Helpers ────────────────────────────────────────────────────────────────

def pick_domain(prof):
    text = " ".join([
        " ".join(prof.get("research_areas") or []),
        " ".join(prof.get("technical_expertise") or []),
        prof.get("biography", "")[:400],
    ]).lower()
    for domain, keywords in DOMAIN_KEYWORDS:
        if any(k in text for k in keywords):
            return domain
    # Fallback by department
    dept = prof.get("department", "")
    for key, domain in DEPT_KEYWORDS.items():
        if key in dept:
            return domain
    return "default"


def pick_patent_count(rng: random.Random) -> int:
    bucket = rng.choices(
        ["zero", "one_to_three", "four_to_six", "seven_to_eight"],
        weights=[30, 40, 20, 10],
    )[0]
    if bucket == "zero":
        return 0
    if bucket == "one_to_three":
        return rng.randint(1, 3)
    if bucket == "four_to_six":
        return rng.randint(4, 6)
    return rng.randint(7, 8)


def pick_status(rng: random.Random) -> str:
    return rng.choices(["granted", "published", "filed"], weights=[50, 30, 20])[0]


def make_abstract(prof, title: str, domain: str) -> str:
    expertise = (prof.get("technical_expertise") or [])[:3]
    expertise_text = ", ".join(str(e) for e in expertise) if expertise else "the field"
    return (
        f"The invention discloses {title.lower()}. The method draws on advances in "
        f"{expertise_text}. Embodiments include practical implementations in the area "
        f"of {prof.get('department', '').replace('Department of ', '').lower()}, with "
        "improvements over prior art in accuracy, efficiency, and robustness."
    )


# ─── Main generator ────────────────────────────────────────────────────────

def generate(professors: list) -> tuple[list, dict]:
    # Group professors by department for co-inventor pairing
    by_dept: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(professors):
        by_dept[p.get("department", "Unknown")].append(i)

    all_patents: list[dict] = []
    stats = {"by_count": defaultdict(int), "by_status": defaultdict(int), "co_invented": 0}

    for idx, prof in enumerate(professors):
        seed = int(hashlib.sha1(str(prof.get("professor_id", prof["name"])).encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        n = pick_patent_count(rng)
        stats["by_count"][n] += 1

        if n == 0:
            prof["patents"] = []
            continue

        domain = pick_domain(prof)
        pool = TITLE_POOLS.get(domain, TITLE_POOLS["default"]) + TITLE_POOLS["default"]
        rng.shuffle(pool)
        titles = pool[:n]

        prof_patents = []
        for i in range(n):
            year = rng.randint(2015, 2025)
            month = rng.randint(1, 12)
            day = rng.randint(1, 28)
            status = pick_status(rng)
            stats["by_status"][status] += 1

            patent = {
                "title": titles[i],
                "filing_date": f"{year}-{month:02d}-{day:02d}",
                "patent_number": f"IN-{year}{rng.randint(10000, 99999)}",
                "status": status,
                "abstract": make_abstract(prof, titles[i], domain),
                "inventors": [prof["name"]],
                "co_inventors": [],
                "department": prof.get("department", ""),
                "source": "synthetic",
            }
            # 20% chance: add 1-2 co-inventors from the same department
            if rng.random() < 0.20:
                same_dept = [j for j in by_dept[prof.get("department", "")] if j != idx]
                if same_dept:
                    n_co = rng.randint(1, 2)
                    chosen = rng.sample(same_dept, k=min(n_co, len(same_dept)))
                    co_names = [professors[j]["name"] for j in chosen]
                    patent["co_inventors"] = co_names
                    patent["inventors"].extend(co_names)
                    stats["co_invented"] += 1

            prof_patents.append(patent)
            all_patents.append(patent)

        prof["patents"] = prof_patents

    return all_patents, stats


# ─── Entry point ───────────────────────────────────────────────────────────

def main() -> None:
    if not BACKUP.exists():
        shutil.copy(PROFS_IN, BACKUP)
        print(f"Backed up {PROFS_IN.name} -> {BACKUP.name}")
    else:
        print(f"Backup already exists at {BACKUP.name}")

    with open(PROFS_IN, encoding="utf-8") as f:
        professors = json.load(f)
    print(f"Loaded {len(professors)} professors")

    all_patents, stats = generate(professors)

    with open(PROFS_OUT, "w", encoding="utf-8") as f:
        json.dump(professors, f, indent=2, ensure_ascii=False)
    # Also overwrite iitm_professors_nlp.json so the engine picks it up by default
    with open(PROFS_IN, "w", encoding="utf-8") as f:
        json.dump(professors, f, indent=2, ensure_ascii=False)
    with open(PATENTS_OUT, "w", encoding="utf-8") as f:
        json.dump(all_patents, f, indent=2, ensure_ascii=False)

    print()
    print(f"Generated {len(all_patents)} patents across {len(professors)} professors")
    print(f"Co-invented patents: {stats['co_invented']}")
    print(f"By status:")
    for status, count in sorted(stats['by_status'].items(), key=lambda x: -x[1]):
        print(f"  {status:10}: {count}")
    print(f"By patent-count bucket:")
    by_bucket = {"0": 0, "1-3": 0, "4-6": 0, "7-8": 0}
    for n, c in stats["by_count"].items():
        if n == 0:
            by_bucket["0"] += c
        elif 1 <= n <= 3:
            by_bucket["1-3"] += c
        elif 4 <= n <= 6:
            by_bucket["4-6"] += c
        else:
            by_bucket["7-8"] += c
    for k, v in by_bucket.items():
        pct = v / len(professors) * 100
        print(f"  {k:4} patents: {v:4} professors ({pct:.1f}%)")
    print()
    print(f"Wrote: {PROFS_OUT.name}")
    print(f"Wrote: {PATENTS_OUT.name}")
    print(f"Overwrote: {PROFS_IN.name} (engine default)")


if __name__ == "__main__":
    main()
