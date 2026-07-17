"""
CollabV AI - Profile NLP (Model 2)
====================================
Enriches professor profiles with structured NLP-extracted tags:
  - nlp_tags: technical keywords from publications + research areas
  - domain_scores: relevance to 50 standard research domains (0-1)
  - industry_fit: fit score per industry sector (0-1)
  - expertise_summary: 2-sentence plain English summary

Usage:
    python -m collabv.profile_nlp
    # or
    python collabv/profile_nlp.py
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter
from typing import Dict, List, Tuple


# ─── 50 Standard Research Domains ────────────────────────────────────────────

RESEARCH_DOMAINS = {
    "Machine Learning": [
        "machine learning", "deep learning", "neural network", "reinforcement learning",
        "supervised learning", "unsupervised learning", "random forest", "svm",
        "gradient boosting", "ensemble", "feature engineering", "transfer learning",
        "generative model", "gan", "autoencoder", "bayesian learning",
    ],
    "Natural Language Processing": [
        "natural language", "nlp", "text mining", "sentiment analysis",
        "language model", "transformer", "bert", "gpt", "word embedding",
        "named entity", "machine translation", "speech recognition",
        "chatbot", "conversational", "information extraction", "text classification",
    ],
    "Computer Vision": [
        "computer vision", "image processing", "object detection", "segmentation",
        "image recognition", "face recognition", "video analysis", "optical character",
        "ocr", "convolutional neural", "yolo", "resnet", "image classification",
        "depth estimation", "3d reconstruction", "lidar", "point cloud",
    ],
    "Robotics & Automation": [
        "robot", "autonomous", "manipulation", "path planning", "slam",
        "kinematics", "dynamics control", "actuator", "mechatronics", "ros",
        "motion planning", "swarm", "human robot", "mobile robot", "gripper",
    ],
    "Signal Processing": [
        "signal processing", "dsp", "filter design", "fourier", "spectral",
        "wavelet", "adaptive filter", "kalman filter", "beamforming",
        "array processing", "compressive sensing", "sparse signal",
    ],
    "Wireless Communications": [
        "wireless", "5g", "mimo", "ofdm", "antenna", "rf design",
        "communication system", "modulation", "cognitive radio", "spectrum",
        "channel estimation", "beamforming", "massive mimo", "millimeter wave",
    ],
    "Power Systems": [
        "power system", "power electronics", "power grid", "inverter",
        "converter", "motor drive", "smart grid", "microgrid", "hvdc",
        "renewable integration", "load balancing", "power quality",
    ],
    "VLSI & Semiconductor": [
        "vlsi", "semiconductor", "integrated circuit", "cmos", "fpga",
        "asic", "digital design", "analog circuit", "soc", "mems",
        "nanoelectronic", "transistor", "fabrication", "lithography",
    ],
    "Embedded Systems & IoT": [
        "embedded system", "iot", "internet of things", "microcontroller",
        "firmware", "rtos", "sensor network", "edge computing", "wearable",
        "smart sensor", "low power", "wireless sensor",
    ],
    "Control Systems": [
        "control system", "pid", "robust control", "adaptive control",
        "optimal control", "model predictive", "nonlinear control",
        "feedback", "state estimation", "lyapunov", "stability analysis",
    ],
    "Structural Engineering": [
        "structural", "finite element", "fem", "fea", "stress analysis",
        "vibration", "modal analysis", "buckling", "fatigue", "fracture",
        "composite structure", "earthquake resistant", "structural health",
    ],
    "Geotechnical Engineering": [
        "geotechnical", "soil mechanics", "foundation", "slope stability",
        "tunneling", "ground improvement", "retaining wall", "pile",
        "liquefaction", "bearing capacity",
    ],
    "Transportation Engineering": [
        "transportation", "traffic", "highway", "pavement", "road safety",
        "urban planning", "transit", "logistics", "freight", "connected vehicle",
    ],
    "Water Resources": [
        "water resource", "hydrology", "groundwater", "flood",
        "irrigation", "dam", "watershed", "rainfall", "river",
        "water management", "stormwater",
    ],
    "Environmental Engineering": [
        "environmental", "wastewater", "water treatment", "air quality",
        "pollution", "emission", "remediation", "solid waste",
        "heavy metal", "contaminant", "effluent", "sewage",
    ],
    "Fluid Mechanics & CFD": [
        "fluid mechanics", "cfd", "computational fluid", "turbulence",
        "navier stokes", "boundary layer", "flow simulation", "aerodynamic",
        "hydrodynamic", "multiphase flow", "droplet", "spray",
    ],
    "Heat Transfer & Thermal": [
        "heat transfer", "thermal", "convection", "conduction", "radiation",
        "heat exchanger", "cooling", "boiling", "condensation",
        "thermal management", "hvac", "refrigeration",
    ],
    "Combustion & Propulsion": [
        "combustion", "propulsion", "flame", "fuel", "ignition",
        "detonation", "jet engine", "rocket", "gas turbine", "spray combustion",
    ],
    "Manufacturing & Production": [
        "manufacturing", "machining", "cnc", "forming", "welding",
        "casting", "forging", "grinding", "surface finish", "toleranc",
        "quality control", "lean manufacturing", "industry 4.0",
    ],
    "Additive Manufacturing": [
        "additive manufacturing", "3d print", "selective laser", "powder bed",
        "fused deposition", "stereolithography", "metal printing", "bioprinting",
    ],
    "Metallurgy": [
        "metallurg", "alloy", "steel", "phase transformation", "microstructure",
        "grain", "heat treatment", "quench", "temper", "recrystallization",
        "texture", "deformation", "dislocation",
    ],
    "Materials Characterization": [
        "characterization", "sem", "tem", "xrd", "spectroscopy",
        "microscopy", "diffraction", "raman", "ftir", "afm",
        "mechanical testing", "tensile", "hardness", "nanoindentation",
    ],
    "Corrosion & Surface": [
        "corrosion", "surface", "coating", "thin film", "tribology",
        "wear", "friction", "electrodeposition", "plasma coating",
        "oxidation", "passivation",
    ],
    "Nanomaterials": [
        "nanomaterial", "nanoparticle", "nanotube", "graphene",
        "quantum dot", "nanocomposite", "nanostructure", "nano coating",
        "self assembly", "molecular assembly",
    ],
    "Polymer Science": [
        "polymer", "plastic", "rubber", "elastomer", "polymerization",
        "copolymer", "biodegradable", "bioplastic", "polymer composite",
        "polymer processing", "injection molding",
    ],
    "Catalysis & Reaction Engineering": [
        "catalysis", "catalyst", "reaction engineering", "heterogeneous",
        "homogeneous catalyst", "photocatalysis", "electrocatalysis",
        "reaction kinetics", "reactor design",
    ],
    "Separation & Process Engineering": [
        "separation", "distillation", "membrane", "adsorption",
        "extraction", "chromatography", "filtration", "crystallization",
        "process intensification", "process optimization",
    ],
    "Electrochemistry & Energy Storage": [
        "electrochemistry", "battery", "lithium", "fuel cell",
        "supercapacitor", "electrode", "electrolyte", "energy storage",
        "charging", "hydrogen production", "water splitting",
    ],
    "Biochemistry & Molecular Biology": [
        "biochemistry", "molecular biology", "enzyme", "protein",
        "dna", "rna", "gene expression", "metabolic", "biosynthesis",
        "recombinant", "cloning", "pcr",
    ],
    "Bioprocess Engineering": [
        "bioprocess", "fermentation", "bioreactor", "downstream",
        "cell culture", "scale up", "biorefinery", "bioconversion",
        "microbial", "yeast", "bacteria cultivation",
    ],
    "Drug Discovery & Delivery": [
        "drug discovery", "drug delivery", "pharmaceutical",
        "pharmacokinetic", "formulation", "nanoparticle drug",
        "targeted delivery", "controlled release", "bioavailability",
    ],
    "Bioinformatics & Genomics": [
        "bioinformatics", "genomics", "proteomics", "transcriptomics",
        "sequence analysis", "gene editing", "crispr", "genome",
        "phylogenetic", "metagenomics",
    ],
    "Computational Mechanics": [
        "computational mechanics", "finite element", "meshfree",
        "isogeometric", "boundary element", "peridynamics",
        "multiscale", "homogenization", "topology optimization",
    ],
    "Optimization & Operations Research": [
        "optimization", "operations research", "linear programming",
        "integer programming", "metaheuristic", "genetic algorithm",
        "particle swarm", "multi objective", "scheduling", "combinatorial",
    ],
    "Data Analytics & Statistics": [
        "data analytics", "statistical", "regression", "classification",
        "clustering", "dimensionality reduction", "pca", "time series",
        "hypothesis testing", "experimental design", "anova",
    ],
    "Quantum Computing & Physics": [
        "quantum computing", "quantum mechanics", "qubit", "quantum algorithm",
        "quantum information", "entanglement", "quantum simulation",
    ],
    "Condensed Matter Physics": [
        "condensed matter", "solid state", "superconductor", "magnetic",
        "ferroelectric", "dielectric", "phonon", "band structure",
        "topological", "spintronics",
    ],
    "Optics & Photonics": [
        "optics", "photonics", "laser", "fiber optic", "nonlinear optics",
        "spectroscopy", "imaging", "holograph", "diffraction", "interferometry",
    ],
    "Nuclear & Particle Physics": [
        "nuclear", "particle physics", "neutron", "accelerator",
        "high energy", "detector", "scattering", "cross section",
    ],
    "Ocean Engineering": [
        "ocean engineering", "offshore", "subsea", "marine structure",
        "wave energy", "tidal", "mooring", "riser", "floating platform",
        "ship design", "naval architecture", "hydrodynamic load",
    ],
    "Coastal & Port Engineering": [
        "coastal", "port", "breakwater", "sediment transport",
        "beach erosion", "wave propagation", "harbor", "dredging",
    ],
    "Aerospace Structures": [
        "aerospace structure", "aircraft design", "wing", "fuselage",
        "aeroelasticity", "flutter", "composite laminate", "sandwich panel",
    ],
    "Navigation & Guidance": [
        "navigation", "guidance", "inertial", "gps", "ins",
        "trajectory", "orbit", "satellite navigation", "autopilot",
    ],
    "Biomechanics": [
        "biomechanics", "gait analysis", "musculoskeletal", "orthopedic",
        "prosthetic", "implant", "rehabilitation", "ergonomics",
        "human movement", "tissue mechanics",
    ],
    "Medical Devices": [
        "medical device", "diagnostic", "therapeutic device", "biosensor",
        "wearable health", "point of care", "ultrasound", "mri",
        "ct scan", "medical imaging",
    ],
    "Sustainable Energy": [
        "sustainable energy", "solar cell", "wind energy", "photovoltaic",
        "biomass", "biofuel", "geothermal", "wave energy", "tidal energy",
        "energy efficiency", "green building",
    ],
    "Cybersecurity": [
        "cybersecurity", "network security", "cryptography", "encryption",
        "malware", "intrusion detection", "firewall", "vulnerability",
        "penetration testing", "secure protocol", "privacy",
    ],
    "Cloud & Distributed Computing": [
        "cloud computing", "distributed system", "parallel computing",
        "edge computing", "fog computing", "microservice", "containerization",
        "serverless", "load balancing",
    ],
    "Database & Information Systems": [
        "database", "information retrieval", "knowledge graph",
        "semantic web", "big data", "data warehouse", "nosql",
        "search engine", "indexing",
    ],
    "Design & Product Development": [
        "product design", "industrial design", "cad", "design thinking",
        "human factors", "usability", "user experience", "ergonomic design",
        "rapid prototyping", "design optimization",
    ],
}


# ─── Industry Sectors ────────────────────────────────────────────────────────

INDUSTRY_SECTORS = {
    "Automotive": [
        "automotive", "vehicle", "car", "ev", "electric vehicle", "adas",
        "autonomous driving", "engine", "transmission", "chassis", "tire",
    ],
    "Aerospace & Defence": [
        "aerospace", "defence", "defense", "aviation", "aircraft", "missile",
        "satellite", "uav", "drone", "radar", "military",
    ],
    "IT & Software": [
        "software", "it ", "cloud", "saas", "fintech", "edtech",
        "web", "mobile app", "devops", "agile",
    ],
    "Pharma & Healthcare": [
        "pharma", "drug", "hospital", "medical", "healthcare", "diagnostic",
        "therapeutic", "clinical", "patient", "health tech",
    ],
    "Energy & Power": [
        "energy", "power", "solar", "wind", "oil", "gas", "petroleum",
        "renewable", "grid", "battery", "fuel cell", "hydrogen",
    ],
    "Manufacturing": [
        "manufacturing", "factory", "production", "quality control",
        "supply chain", "lean", "six sigma", "industry 4.0",
    ],
    "Steel & Metals": [
        "steel", "metal", "aluminum", "copper", "alloy", "foundry",
        "smelting", "mining", "ore",
    ],
    "Chemical & Petrochemical": [
        "chemical", "petrochemical", "refinery", "polymer", "catalyst",
        "specialty chemical", "agrochemical", "fertilizer",
    ],
    "Telecom": [
        "telecom", "5g", "wireless", "network operator", "fiber optic",
        "broadband", "spectrum",
    ],
    "Construction & Infrastructure": [
        "construction", "infrastructure", "building", "cement", "real estate",
        "highway", "bridge", "tunnel", "smart city",
    ],
    "Water & Environment": [
        "water", "wastewater", "environment", "pollution", "waste management",
        "recycling", "sustainability", "climate",
    ],
    "Marine & Ocean": [
        "marine", "ocean", "offshore", "ship", "port", "coastal",
        "subsea", "naval", "maritime", "fishing",
    ],
    "Biotech & Life Sciences": [
        "biotech", "life science", "genomic", "bioinformatics",
        "fermentation", "enzyme", "protein", "vaccine",
    ],
    "Electronics & Semiconductor": [
        "electronics", "semiconductor", "chip", "circuit", "sensor",
        "embedded", "iot", "wearable", "display",
    ],
    "Agriculture & Food": [
        "agriculture", "farming", "crop", "soil", "food processing",
        "dairy", "fisheries", "agritech", "precision agriculture",
    ],
    "Finance & Banking": [
        "finance", "banking", "insurance", "fintech", "risk management",
        "algorithmic trading", "blockchain", "payment",
    ],
    "Robotics": [
        "robot", "automation", "cobots", "industrial robot", "agv",
        "warehouse automation", "pick and place",
    ],
    "Space Technology": [
        "space", "launch vehicle", "satellite", "orbit", "payload",
        "remote sensing", "earth observation",
    ],
}


# ─── NLP Tag Extraction ─────────────────────────────────────────────────────

# Technical keywords to look for in publications and research areas
TECH_KEYWORDS = [
    # Methods
    "finite element", "boundary element", "monte carlo", "molecular dynamics",
    "density functional", "cfd", "les", "dns", "rans", "fem", "bem",
    "machine learning", "deep learning", "neural network", "cnn", "rnn", "lstm",
    "transformer", "attention mechanism", "reinforcement learning", "gan",
    "transfer learning", "federated learning", "graph neural",
    "optimization", "genetic algorithm", "particle swarm", "simulated annealing",
    "bayesian", "gaussian process", "kriging", "surrogate model",
    "kalman filter", "particle filter", "state estimation",
    "pid", "mpc", "lqr", "robust control", "adaptive control",
    # Materials & processes
    "nanoparticle", "graphene", "carbon nanotube", "quantum dot",
    "thin film", "coating", "plasma", "laser", "sintering",
    "polymerization", "crystallization", "precipitation",
    "heat treatment", "quenching", "tempering", "annealing",
    "machining", "milling", "turning", "grinding", "edm",
    "additive manufacturing", "3d printing", "selective laser",
    "welding", "brazing", "soldering",
    # Bio
    "pcr", "elisa", "western blot", "flow cytometry",
    "crispr", "gene editing", "cloning", "transfection",
    "fermentation", "chromatography", "mass spectrometry",
    "protein engineering", "enzyme kinetics", "metabolic engineering",
    # Instruments
    "sem", "tem", "afm", "xrd", "ftir", "raman", "nmr",
    "hplc", "gc ms", "uv vis", "fluorescence",
    # Software/tools
    "matlab", "simulink", "python", "tensorflow", "pytorch",
    "ansys", "abaqus", "comsol", "openfoam", "fluent",
    "autocad", "solidworks", "catia", "labview",
    "ros", "gazebo", "opencv", "scikit learn",
    "fpga", "verilog", "vhdl", "cadence", "synopsys",
]


def _extract_nlp_tags(text: str) -> List[str]:
    """Extract technical keyword tags from combined text."""
    text_lower = text.lower()
    found = []
    for kw in TECH_KEYWORDS:
        if kw in text_lower:
            found.append(kw)
    return sorted(set(found))


def _score_domains(text: str) -> Dict[str, float]:
    """Score relevance to each of the 50 research domains."""
    text_lower = text.lower()
    scores = {}
    for domain, keywords in RESEARCH_DOMAINS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            # Normalize: ratio of matched keywords, capped at 1.0
            scores[domain] = round(min(hits / max(len(keywords) * 0.3, 1), 1.0), 3)
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


def _score_industries(text: str, dept: str) -> Dict[str, float]:
    """Score how well this professor fits each industry sector."""
    text_lower = (text + " " + dept).lower()
    scores = {}
    for industry, keywords in INDUSTRY_SECTORS.items():
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            scores[industry] = round(min(hits / max(len(keywords) * 0.25, 1), 1.0), 3)
    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


def _generate_summary(prof: dict, top_domains: List[str], top_industries: List[str]) -> str:
    """Generate a 2-sentence expertise summary."""
    name = prof["name"]
    dept = prof.get("department", "").replace("Department of ", "")
    designation = prof.get("designation", "Professor")

    domain_str = ", ".join(top_domains[:3]) if top_domains else dept
    industry_str = ", ".join(top_industries[:3]) if top_industries else "academic research"

    s1 = f"{name} is a {designation} in {dept} with expertise in {domain_str}."
    s2 = f"Their research is relevant to {industry_str} applications."
    return f"{s1} {s2}"


# ─── Main Processing ────────────────────────────────────────────────────────

def enrich_professors(input_path: str = None, output_path: str = None):
    """Run NLP enrichment on all professors."""
    if input_path is None:
        input_path = str(Path(__file__).parent.parent / "iitm_professors_enriched.json")
    if output_path is None:
        output_path = str(Path(__file__).parent.parent / "iitm_professors_nlp.json")

    with open(input_path, encoding="utf-8") as f:
        professors = json.load(f)

    print(f"Processing {len(professors)} professors with NLP enrichment...")

    for i, prof in enumerate(professors):
        # Build combined text from all relevant fields
        parts = []
        parts.extend(prof.get("research_areas", []))
        parts.extend(prof.get("publications", [])[:5])
        parts.extend(prof.get("technical_expertise", []))
        parts.append(prof.get("biography", ""))
        tags = prof.get("matching_tags", {})
        parts.extend(tags.get("research_domain_tags", []))
        parts.extend(tags.get("tech_skill_tags", []))
        parts.extend(prof.get("industry_exposure", []))
        combined = " ".join(str(x) for x in parts if x)

        dept = prof.get("department", "")

        # Extract NLP fields
        nlp_tags = _extract_nlp_tags(combined)
        domain_scores = _score_domains(combined)
        industry_fit = _score_industries(combined, dept)

        top_domains = [d for d, s in domain_scores.items() if s > 0.1][:5]
        top_industries = [ind for ind, s in industry_fit.items() if s > 0.1][:5]

        summary = _generate_summary(prof, top_domains, top_industries)

        # Add to professor record
        prof["nlp_tags"] = nlp_tags
        prof["domain_scores"] = domain_scores
        prof["industry_fit"] = industry_fit
        prof["expertise_summary"] = summary

        if (i + 1) % 100 == 0:
            print(f"  Processed {i+1}/{len(professors)}...")

    # Save
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(professors, f, indent=2, ensure_ascii=False)

    # Stats
    has_tags = sum(1 for p in professors if p.get("nlp_tags"))
    has_domains = sum(1 for p in professors if p.get("domain_scores"))
    has_industry = sum(1 for p in professors if p.get("industry_fit"))
    avg_tags = sum(len(p.get("nlp_tags", [])) for p in professors) / len(professors)
    avg_domains = sum(len(p.get("domain_scores", {})) for p in professors) / len(professors)

    print(f"\nNLP Enrichment Complete:")
    print(f"  Professors with NLP tags   : {has_tags}/{len(professors)}")
    print(f"  Professors with domains    : {has_domains}/{len(professors)}")
    print(f"  Professors with industry   : {has_industry}/{len(professors)}")
    print(f"  Avg NLP tags per professor : {avg_tags:.1f}")
    print(f"  Avg domains per professor  : {avg_domains:.1f}")
    print(f"  Saved to: {output_path}")


if __name__ == "__main__":
    enrich_professors()
