"""
CollabV AI - Matching Engine 2: Professor → Company
=====================================================
Reverse of matching_engine.py: given a professor profile,
rank all company project listings by fit.

Company pool = optional static seed spreadsheet (100_Companies_Collaboration
_Schema.xlsx, if present - kept only for demo continuity) UNIONED with every
company registered live through the Company Dashboard (the company_profiles
DB table, via patent_marketplace_db.list_company_profiles()). The live half
is read fresh on every access (ProfessorMatchEngine.companies is a property,
not a cached attribute) so a company that registers today shows up in this
reverse-match view immediately, no restart needed - and the engine works
fine with zero seed data if the spreadsheet is deleted entirely.

Scoring weights (sum = 100):
  Research domain similarity      30%
  Technical skills & technologies 25%
  AI/ML methodologies             20%
  Publications / project relevance 15%
  Industry / application domain   10%

Match levels:
  Excellent  90-100
  Strong     75-89
  Moderate   60-74
  Weak       40-59
  Poor       <40

Run standalone:
  python -m collabv.matching_engine_2 --professor IITM-0201
"""

from __future__ import annotations

import ast
import re
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# ─── Constants ───────────────────────────────────────────────────────────────

COMPANIES_FILE = os.getenv(
    "COMPANIES_FILE",
    str(Path(__file__).parent.parent / "100_Companies_Collaboration_Schema.xlsx"),
)

# Domain keyword map: each key is a research domain name, value = trigger keywords
DOMAIN_KEYWORD_MAP: Dict[str, List[str]] = {
    "Machine Learning":            ["machine learning", "ml", "prediction", "classification",
                                    "regression", "random forest", "gradient boosting", "xgboost"],
    "Deep Learning":               ["deep learning", "neural network", "cnn", "rnn", "lstm",
                                    "transformer", "attention mechanism", "backpropagation"],
    "Natural Language Processing": ["nlp", "natural language", "text", "language model", "bert",
                                    "gpt", "chatbot", "sentiment", "ner", "summarization",
                                    "translation", "question answering", "dialogue"],
    "Computer Vision":             ["computer vision", "image", "video", "object detection",
                                    "segmentation", "ocr", "face recognition", "visual"],
    "Reinforcement Learning":      ["reinforcement", "reward", "agent", "policy", "q-learning",
                                    "markov", "bandit"],
    "Generative AI / LLM":        ["llm", "large language", "generative ai", "gpt", "rag",
                                    "prompt", "fine-tun", "diffusion", "stable diffusion"],
    "Data Science & Analytics":   ["data science", "analytics", "data analysis", "statistics",
                                    "pandas", "numpy", "visualization", "dashboard", "bi"],
    "Bioinformatics":              ["genomics", "bioinformatics", "gene", "protein", "drug",
                                    "molecular", "dna", "rna", "sequence"],
    "Cloud & Distributed":        ["cloud", "distributed", "microservice", "kubernetes", "docker",
                                    "aws", "gcp", "azure", "serverless", "scalab"],
    "Cybersecurity & AI":         ["cybersecurity", "security", "encryption", "threat",
                                    "vulnerability", "anomaly detection", "intrusion", "fraud"],
    "Robotics & Automation":      ["robot", "automation", "iot", "edge", "sensor", "embedded",
                                    "actuator", "drone", "autonomous"],
    "Healthcare AI":              ["health", "medical", "clinical", "patient", "diagnosis",
                                   "ehr", "hospital", "telemedicine", "radiology"],
    "FinTech AI":                 ["fintech", "finance", "credit", "fraud detection", "trading",
                                   "risk", "insurance", "banking", "payment"],
    "EdTech AI":                  ["education", "learning", "tutoring", "assessment", "skill",
                                   "course", "student", "adaptive", "vernacular"],
    "Optimization":               ["optimization", "operations research", "scheduling", "planning",
                                   "supply chain", "logistics", "route", "linear programming"],
    "Signal Processing":          ["signal", "audio", "speech", "sound", "frequency", "acoustic",
                                   "waveform", "spectrum"],
    "Control Systems":            ["control", "pid", "feedback", "navigation", "guidance",
                                   "trajectory", "dynamics"],
    "Fluid Mechanics & CFD":      ["fluid", "cfd", "simulation", "aerodynamics", "turbulence",
                                   "flow", "computational fluid"],
    "Materials Science":          ["material", "nano", "composite", "alloy", "polymer",
                                   "semiconductor", "coating"],
    "Structural Engineering":     ["structural", "civil", "concrete", "bridge", "building",
                                   "geotechnical", "earthquake"],
}

AI_TECHNIQUES: List[str] = [
    "Transformer Architecture", "Attention Mechanism", "BERT / RoBERTa",
    "GPT / LLM Fine-tuning", "Multi-task Learning", "Zero-shot Learning",
    "Few-shot Prompting", "RAG (Retrieval Augmented Generation)",
    "Seq2Seq Models", "Named Entity Recognition", "Sentiment Analysis",
    "Machine Translation", "Summarization", "Speech-to-Text",
    "Contrastive Learning", "Knowledge Distillation", "Active Learning",
    "Federated Learning", "Graph Neural Networks", "Variational Autoencoders",
    "Generative Adversarial Networks", "Diffusion Models", "Object Detection",
    "Semantic Segmentation", "Optical Flow", "Depth Estimation",
    "Reinforcement Learning from Human Feedback", "Chain-of-Thought Prompting",
]

# Generic ML signal keywords — used for density bonus
ML_KEYWORDS: set = {
    "machine learning", "deep learning", "neural network", "ai", "artificial intelligence",
    "nlp", "natural language", "language model", "llm", "bert", "gpt", "transformer",
    "computer vision", "reinforcement learning", "generative ai", "rag", "embeddings",
    "recommendation", "prediction", "classification", "regression", "clustering",
    "pytorch", "tensorflow", "scikit", "hugging face", "data science", "analytics",
    "algorithm", "model training", "transfer learning", "fine-tuning", "zero-shot",
    "few-shot", "question answering", "information extraction", "knowledge graph",
    "semantic search", "vector database", "multimodal", "foundation model",
}


# ─── Data classes ────────────────────────────────────────────────────────────

@dataclass
class CompanyProject:
    company_name: str
    industry_domain: str
    sector: str
    technical_area: str
    problem_type: str
    required_expertise: str
    tech_stack: str
    application_area: str
    research_level: str
    collaboration_type: str
    expected_outcome: str
    project_description: str
    challenges: str
    deliverables: str
    location: str
    budget: str
    timeline: str
    company_id: str = ""  # set for live company_profiles rows; blank for seed-spreadsheet rows

    @property
    def title(self) -> str:
        return f"{self.sector} — {self.technical_area}"

    @property
    def full_text(self) -> str:
        return " ".join([
            self.technical_area, self.required_expertise, self.tech_stack,
            self.application_area, self.project_description, self.challenges,
            self.expected_outcome, self.deliverables, self.problem_type,
            self.industry_domain, self.sector,
        ]).lower()


@dataclass
class CompanyMatchResult:
    rank: int
    company_name: str
    project_title: str
    industry_domain: str
    sector: str
    location: str
    budget: str
    timeline: str
    collaboration_type: str

    score: float
    match_level: str
    recommendation: str
    confidence_score: int

    # Score breakdown (each out of its max)
    research_domain_score: float   # /30
    technical_skills_score: float  # /25
    ai_methods_score: float        # /20
    publication_score: float       # /15
    industry_domain_score: float   # /10

    matching_research_areas: List[str] = field(default_factory=list)
    matching_skills: List[str] = field(default_factory=list)
    matching_technologies: List[str] = field(default_factory=list)
    matching_ai_techniques: List[str] = field(default_factory=list)
    matching_keywords: List[str] = field(default_factory=list)
    missing_skills: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    professor_contribution: str = ""
    student_roles: str = ""
    collaboration_potential: str = ""
    company_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "company_id": self.company_id,
            "company_name": self.company_name,
            "project_title": self.project_title,
            "industry_domain": self.industry_domain,
            "sector": self.sector,
            "location": self.location,
            "budget": self.budget,
            "timeline": self.timeline,
            "collaboration_type": self.collaboration_type,
            "score": self.score,
            "match_level": self.match_level,
            "recommendation": self.recommendation,
            "confidence_score": self.confidence_score,
            "score_breakdown": {
                "research_domain": self.research_domain_score,
                "technical_skills": self.technical_skills_score,
                "ai_methods": self.ai_methods_score,
                "publications": self.publication_score,
                "industry_domain": self.industry_domain_score,
            },
            "matching_research_areas": self.matching_research_areas,
            "matching_skills": self.matching_skills,
            "matching_technologies": self.matching_technologies,
            "matching_ai_techniques": self.matching_ai_techniques,
            "matching_keywords": self.matching_keywords,
            "missing_skills": self.missing_skills,
            "reasons": self.reasons,
            "professor_contribution": self.professor_contribution,
            "student_roles": self.student_roles,
            "collaboration_potential": self.collaboration_potential,
        }


@dataclass
class ProfessorMatchResponse:
    match_id: str
    professor_id: str
    professor_name: str
    department: str
    designation: str
    top_domains: List[str]
    results: List[CompanyMatchResult]
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "match_id": self.match_id,
            "professor_id": self.professor_id,
            "professor_name": self.professor_name,
            "department": self.department,
            "designation": self.designation,
            "top_domains": self.top_domains,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
        }


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _safe_list(v: Any) -> List[str]:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            parsed = ast.literal_eval(v)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except Exception:
            pass
        return [v] if v.strip() else []
    return []


def _safe_dict(v: Any) -> Dict[str, float]:
    if isinstance(v, dict):
        return {k: float(val) for k, val in v.items()}
    if isinstance(v, str):
        try:
            parsed = ast.literal_eval(v)
            if isinstance(parsed, dict):
                return {k: float(val) for k, val in parsed.items()}
        except Exception:
            pass
    return {}


def _tokenize(text: str) -> set:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def _match_level(score: float) -> str:
    if score >= 90: return "Excellent"
    if score >= 75: return "Strong"
    if score >= 60: return "Moderate"
    if score >= 40: return "Weak"
    return "Poor"


def _recommendation(score: float) -> str:
    if score >= 85: return "Highly Recommended"
    if score >= 70: return "Recommended"
    if score >= 50: return "Consider"
    return "Not Recommended"


def _confidence(score: float, n_missing: int) -> int:
    base = min(95.0, score + 5.0) - n_missing * 5.0
    return max(30, round(base))


# ─── Company loader ───────────────────────────────────────────────────────────

def load_companies(filepath: str = COMPANIES_FILE) -> List[CompanyProject]:
    df = pd.read_excel(filepath, header=2)
    projects: List[CompanyProject] = []
    for _, row in df.iterrows():
        name = str(row.get("Company Name", "")).strip()
        if not name or name == "Company Name":
            continue
        projects.append(CompanyProject(
            company_name=name,
            industry_domain=str(row.get("Industry Domain", "")).strip(),
            sector=str(row.get("Industry Sector", "")).strip(),
            technical_area=str(row.get("Technical Area", "")).strip(),
            problem_type=str(row.get("Problem Type", "")).strip(),
            required_expertise=str(row.get("Required Expertise", "")).strip(),
            tech_stack=str(row.get("Technology Stack", "")).strip(),
            application_area=str(row.get("Application Area", "")).strip(),
            research_level=str(row.get("Research Level", "")).strip(),
            collaboration_type=str(row.get("Collaboration Type", "")).strip(),
            expected_outcome=str(row.get("Expected Outcome", "")).strip(),
            project_description=str(row.get("Project Description", "")).strip(),
            challenges=str(row.get("Challenges", "")).strip(),
            deliverables=str(row.get("Desired Deliverables", "")).strip(),
            location=str(row.get("Location", "")).strip(),
            budget=str(row.get("Budget", "")).strip(),
            timeline=str(row.get("Timeline", "")).strip(),
        ))
    return projects


def _joined(profile: Dict[str, Any], key: str) -> str:
    return ", ".join(str(v) for v in (profile.get(key) or []))


def _company_profile_to_project(profile: Dict[str, Any]) -> CompanyProject:
    """Map a live company_profiles DB row (Company Dashboard registration
    shape) onto the Excel-schema CompanyProject the scorer expects. The two
    shapes don't align 1:1 - the registration form doesn't collect
    budget/timeline/location/collaboration_type/research_level, so those are
    left blank for live companies rather than guessed."""
    return CompanyProject(
        company_id=str(profile.get("company_id", "")),
        company_name=profile.get("company_name", ""),
        industry_domain=profile.get("industry", ""),
        sector=profile.get("business_domain", "") or profile.get("category", ""),
        technical_area=_joined(profile, "focus_areas") or _joined(profile, "technologies_used"),
        problem_type=profile.get("innovation_challenges", ""),
        required_expertise=_joined(profile, "research_interests"),
        tech_stack=_joined(profile, "tech_stack") or _joined(profile, "technologies_used"),
        application_area=_joined(profile, "preferred_collaboration_areas") or profile.get("market_segment", ""),
        research_level="",
        collaboration_type="",
        expected_outcome=profile.get("strategic_goals", ""),
        project_description=profile.get("description", ""),
        challenges=profile.get("innovation_challenges", ""),
        deliverables=_joined(profile, "existing_projects"),
        location="",
        budget="",
        timeline="",
    )


# ─── Core Engine ─────────────────────────────────────────────────────────────

class ProfessorMatchEngine:
    """Match a professor profile against company project listings - the
    static seed spreadsheet (if present) plus every live-registered company
    from the database."""

    def __init__(self, companies_file: str = COMPANIES_FILE, db_path: Optional[str] = None):
        self.db_path = db_path
        try:
            self._seed_companies: List[CompanyProject] = (
                load_companies(companies_file) if companies_file and Path(companies_file).exists() else []
            )
        except Exception as e:
            print(f"[matching_engine_2] Failed to load seed companies from {companies_file}: {e}")
            self._seed_companies = []

    @property
    def companies(self) -> List[CompanyProject]:
        """Read fresh on every access so a newly-registered company shows up
        immediately - no restart, no manual cache refresh."""
        return self._seed_companies + self._load_live_companies()

    def _load_live_companies(self) -> List[CompanyProject]:
        try:
            from .patent_marketplace_db import list_company_profiles
            return [_company_profile_to_project(p) for p in list_company_profiles(self.db_path)]
        except Exception as e:
            print(f"[matching_engine_2] Failed to load live company profiles: {e}")
            return []

    # ── Professor profile extraction ─────────────────────────────────────────

    def _extract_profile(self, prof: Dict[str, Any]) -> Dict[str, Any]:
        domain_scores = _safe_dict(prof.get("domain_scores", {}))
        industry_fit  = _safe_dict(prof.get("industry_fit", {}))
        research      = _safe_list(prof.get("research_areas", []))
        expertise     = _safe_list(prof.get("technical_expertise", []))
        nlp_tags      = _safe_list(prof.get("nlp_tags", []))
        pubs          = _safe_list(prof.get("publications", []))
        patents       = _safe_list(prof.get("patents", []))
        ind_exp       = _safe_list(prof.get("industry_exposure", []))
        biography     = str(prof.get("biography", ""))
        summary       = str(prof.get("expertise_summary", ""))

        # Build sorted domain list
        top_domains = sorted(domain_scores.items(), key=lambda x: -x[1])

        # Aggregate professor text
        prof_text = " ".join([
            biography, summary,
            " ".join(research), " ".join(expertise), " ".join(nlp_tags),
            " ".join(str(k) for k in domain_scores.keys()),
            " ".join(str(k) for k in industry_fit.keys()),
            " ".join(pubs), " ".join(str(p) for p in patents),
            " ".join(ind_exp),
        ]).lower()

        # Infer known skills / technologies from tags and expertise
        all_skills  = expertise + nlp_tags + research
        skill_text  = " ".join(all_skills).lower()

        return {
            "domain_scores": domain_scores,
            "industry_fit": industry_fit,
            "top_domains": top_domains,
            "research": research,
            "expertise": expertise,
            "nlp_tags": nlp_tags,
            "pubs": pubs,
            "patents": patents,
            "ind_exp": ind_exp,
            "biography": biography,
            "summary": summary,
            "prof_text": prof_text,
            "prof_tokens": _tokenize(prof_text),
            "skill_text": skill_text,
            "skill_tokens": _tokenize(skill_text),
            "pub_tokens": _tokenize(" ".join(pubs) + " ".join(str(p) for p in patents)),
        }

    # ── Score one company ─────────────────────────────────────────────────────

    def _score(
        self, company: CompanyProject, profile: Dict[str, Any]
    ) -> Tuple[float, Dict[str, float], List[str], List[str], List[str], List[str], List[str], List[str]]:
        proj_text   = company.full_text
        proj_tokens = _tokenize(proj_text)

        matched_domains:    List[str] = []
        matched_skills:     List[str] = []
        matched_techs:      List[str] = []
        matched_ai:         List[str] = []
        matched_kw:         List[str] = []
        missing_skills:     List[str] = []

        # ── 1. Research domain similarity (30%) ──────────────────────────────
        dom_raw = 0.0
        for domain_name, kws in DOMAIN_KEYWORD_MAP.items():
            kw_hits = sum(1 for k in kws if k in proj_text)
            if kw_hits == 0:
                continue
            # Weight by professor's own domain score
            prof_weight = profile["domain_scores"].get(domain_name, 0.15)
            dom_raw += prof_weight * (kw_hits / len(kws))
            matched_domains.append(domain_name)
        research_domain_score = min(30.0, dom_raw * 30)

        # ── 2. Technical skills & technologies (25%) ─────────────────────────
        skill_hits = profile["skill_tokens"] & proj_tokens
        tech_score_raw = min(1.0, len(skill_hits) / max(len(profile["skill_tokens"]) * 0.12, 1))
        technical_skills_score = tech_score_raw * 25

        # Surface matched skill names
        for s in profile["expertise"]:
            s_lower = s.lower()
            if any(w in proj_text for w in re.findall(r"[a-z]{3,}", s_lower)):
                matched_skills.append(s)
        for t in profile["nlp_tags"]:
            if t.lower() in proj_text:
                matched_techs.append(t.upper())

        # ── 3. AI/ML methodologies (20%) ─────────────────────────────────────
        ai_hits = 0
        for tech in AI_TECHNIQUES:
            tech_words = re.findall(r"[a-z]{3,}", tech.lower())
            if any(w in proj_text for w in tech_words):
                ai_hits += 1
                matched_ai.append(tech)
        ai_methods_score = min(20.0, (ai_hits / max(len(AI_TECHNIQUES) * 0.10, 1)) * 20)

        # ── 4. Publications / project relevance (15%) ────────────────────────
        pub_hits = profile["pub_tokens"] & proj_tokens
        publication_score = min(15.0, (len(pub_hits) / max(len(profile["pub_tokens"]) * 0.06, 1)) * 15)

        # ── 5. Industry / application domain overlap (10%) ───────────────────
        ind_raw = 0.0
        for ind, fit in profile["industry_fit"].items():
            ind_words = _tokenize(ind)
            if ind_words & proj_tokens:
                ind_raw += float(fit)
        industry_domain_score = min(10.0, (ind_raw / max(len(profile["industry_fit"]), 1)) * 10)

        total = (research_domain_score + technical_skills_score +
                 ai_methods_score + publication_score + industry_domain_score)

        # ML-density bonus: if project heavily uses ML keywords
        ml_density = len(ML_KEYWORDS & proj_tokens) / max(len(proj_tokens), 1)
        # Weight the bonus by professor's ML/NLP strength
        ml_strength = (profile["domain_scores"].get("Machine Learning", 0) +
                       profile["domain_scores"].get("Natural Language Processing", 0) +
                       profile["domain_scores"].get("Deep Learning", 0)) / 3.0
        if ml_density > 0.04 and ml_strength > 0.3:
            total = min(100.0, total * (1.0 + 0.12 * ml_strength))

        # Penalize if project requires hard skills the professor clearly lacks
        req_text = (company.required_expertise + " " + company.technical_area).lower()
        non_ml_skills = [
            "embedded", "matlab", "cad", "vlsi", "fpga", "verilog", "hardware",
            "ansys", "solidworks", "civil", "structural", "geotechnical", "chemical",
            "pharmaceutical", "genomics", "bioinformatics", "catia",
        ]
        for skill in non_ml_skills:
            if skill in req_text and skill not in profile["prof_text"]:
                missing_skills.append(skill)
                total *= 0.82

        # Matched keywords
        generic_kws = ["ai", "ml", "nlp", "deep learning", "language model",
                       "neural network", "chatbot", "generative", "llm",
                       "multilingual", "text", "speech", "vision", "embeddings"]
        for kw in generic_kws:
            if kw in proj_text:
                matched_kw.append(kw)

        breakdown = {
            "research_domain": round(research_domain_score, 1),
            "technical_skills": round(technical_skills_score, 1),
            "ai_methods": round(ai_methods_score, 1),
            "publications": round(publication_score, 1),
            "industry_domain": round(industry_domain_score, 1),
        }

        return round(total, 1), breakdown, matched_domains, matched_skills, matched_techs, matched_ai, matched_kw, missing_skills

    # ── Build reason bullets ──────────────────────────────────────────────────

    def _build_reasons(
        self,
        company: CompanyProject,
        score: float,
        matched_domains: List[str],
        matched_skills: List[str],
        matched_ai: List[str],
        matched_techs: List[str],
        matched_kw: List[str],
        missing_skills: List[str],
    ) -> List[str]:
        reasons: List[str] = []
        if matched_domains:
            reasons.append(f"Research domain overlap: {', '.join(matched_domains[:3])}")
        if matched_ai:
            reasons.append(f"AI/ML technique alignment: {', '.join(matched_ai[:3])}")
        if matched_skills:
            reasons.append(f"Core skill match: {', '.join(matched_skills[:3])}")
        if matched_techs:
            reasons.append(f"Technology stack overlap: {', '.join(matched_techs[:3])}")
        if matched_kw:
            reasons.append(f"Keyword coverage: {', '.join(matched_kw[:4])}")
        if not missing_skills:
            reasons.append("No critical skill gaps detected in professor profile")
        else:
            reasons.append(f"Partial gap in: {', '.join(missing_skills[:3])} (outside core domain)")
        return reasons[:6]

    # ── Contribution / student roles / collab potential ───────────────────────

    def _build_contribution(
        self, company: CompanyProject, matched_ai: List[str], matched_domains: List[str]
    ) -> str:
        ai_tech = matched_ai[0] if matched_ai else "ML/NLP models"
        domain = matched_domains[0] if matched_domains else "AI"
        return (
            f"Lead {domain} research design; architect and implement {ai_tech} solutions; "
            f"supervise MTech/PhD student teams; co-publish findings; advise on "
            f"{company.sector} AI strategy and benchmark creation."
        )

    def _build_collab_potential(self, score: float) -> str:
        if score >= 80:
            return "Joint Research Agreement (JRA) with IP sharing; Sponsored Research; co-publication in top venues"
        if score >= 65:
            return "Sponsored Research or Consultancy engagement; student internship pipeline"
        if score >= 50:
            return "Exploratory consultancy or semester student project"
        return "Limited fit — recommend seeking a more domain-specific professor"

    # ── Public API ────────────────────────────────────────────────────────────

    def match(
        self,
        professor: Dict[str, Any],
        top_k: Optional[int] = None,
    ) -> List[CompanyMatchResult]:
        """Score all companies against the professor. Returns ranked list."""
        profile = self._extract_profile(professor)
        scored: List[Tuple[float, CompanyProject, Dict, List, List, List, List, List, List]] = []

        for company in self.companies:
            score, breakdown, m_dom, m_skills, m_techs, m_ai, m_kw, missing = \
                self._score(company, profile)
            scored.append((score, company, breakdown, m_dom, m_skills, m_techs, m_ai, m_kw, missing))

        scored.sort(key=lambda x: -x[0])

        results: List[CompanyMatchResult] = []
        for rank, (score, company, breakdown, m_dom, m_skills, m_techs, m_ai, m_kw, missing) in \
                enumerate(scored[:top_k] if top_k else scored, 1):
            reasons = self._build_reasons(
                company, score, m_dom, m_skills, m_ai, m_techs, m_kw, missing)
            results.append(CompanyMatchResult(
                rank=rank,
                company_id=company.company_id,
                company_name=company.company_name,
                project_title=company.title,
                industry_domain=company.industry_domain,
                sector=company.sector,
                location=company.location,
                budget=company.budget,
                timeline=company.timeline,
                collaboration_type=company.collaboration_type,
                score=score,
                match_level=_match_level(score),
                recommendation=_recommendation(score),
                confidence_score=_confidence(score, len(missing)),
                research_domain_score=breakdown["research_domain"],
                technical_skills_score=breakdown["technical_skills"],
                ai_methods_score=breakdown["ai_methods"],
                publication_score=breakdown["publications"],
                industry_domain_score=breakdown["industry_domain"],
                matching_research_areas=m_dom,
                matching_skills=m_skills[:8],
                matching_technologies=m_techs[:8],
                matching_ai_techniques=m_ai[:6],
                matching_keywords=m_kw,
                missing_skills=missing,
                reasons=reasons,
                professor_contribution=self._build_contribution(company, m_ai, m_dom),
                student_roles="MTech/PhD researcher · Data engineer · ML engineer · NLP specialist · Research intern",
                collaboration_potential=self._build_collab_potential(score),
            ))

        return results

    def match_summary(self, results: List[CompanyMatchResult]) -> Dict[str, Any]:
        if not results:
            return {}
        scores = [r.score for r in results]
        dist = {"excellent": 0, "strong": 0, "moderate": 0, "weak": 0, "poor": 0}
        for s in scores:
            if s >= 90:   dist["excellent"] += 1
            elif s >= 75: dist["strong"] += 1
            elif s >= 60: dist["moderate"] += 1
            elif s >= 40: dist["weak"] += 1
            else:         dist["poor"] += 1
        return {
            "total": len(results),
            "avg_score": round(sum(scores) / len(scores), 1),
            "highest_score": round(max(scores), 1),
            "lowest_score": round(min(scores), 1),
            "distribution": dist,
            "recommended_count":     sum(1 for s in scores if s >= 70),
            "consider_count":        sum(1 for s in scores if 50 <= s < 70),
            "not_recommended_count": sum(1 for s in scores if s < 50),
        }


# ─── Module-level singleton (lazy) ───────────────────────────────────────────

_engine: Optional[ProfessorMatchEngine] = None


def get_engine(db_path: Optional[str] = None) -> ProfessorMatchEngine:
    global _engine
    if _engine is None:
        _engine = ProfessorMatchEngine(db_path=db_path)
    elif db_path is not None:
        _engine.db_path = db_path
    return _engine


def run_professor_match(
    professor: Dict[str, Any],
    top_k: Optional[int] = None,
    match_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> ProfessorMatchResponse:
    import uuid
    engine = get_engine(db_path=db_path)
    results = engine.match(professor, top_k=top_k)
    summary = engine.match_summary(results)
    domain_scores = _safe_dict(professor.get("domain_scores", {}))
    top_domains = [k for k, _ in sorted(domain_scores.items(), key=lambda x: -x[1])[:6]]
    return ProfessorMatchResponse(
        match_id=match_id or f"PM-{uuid.uuid4().hex[:8].upper()}",
        professor_id=str(professor.get("professor_id", "")),
        professor_name=str(professor.get("name", "")),
        department=str(professor.get("department", "")).replace("Department of ", ""),
        designation=str(professor.get("designation", "")),
        top_domains=top_domains,
        results=results,
        summary=summary,
    )


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, sys

    parser = argparse.ArgumentParser(description="Professor→Company match engine")
    parser.add_argument("--professor", default="IITM-0201", help="Professor ID")
    parser.add_argument("--top-k", type=int, default=10, help="Top-k companies to show")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    args = parser.parse_args()

    profs_file = Path(__file__).parent.parent / "iitm_professors_nlp.json"
    with open(profs_file, encoding="utf-8") as f:
        all_profs = json.load(f)

    prof = next((p for p in all_profs if p["professor_id"] == args.professor), None)
    if not prof:
        print(f"Professor {args.professor} not found.", file=sys.stderr)
        sys.exit(1)

    resp = run_professor_match(prof, top_k=args.top_k)

    if args.json:
        print(json.dumps(resp.to_dict(), ensure_ascii=False, indent=2))
        sys.exit(0)

    print(f"\n{'='*80}")
    print(f"Professor Match Report — {resp.professor_name}")
    print(f"Department : {resp.department}  |  {resp.designation}")
    print(f"Top Domains: {', '.join(resp.top_domains[:4])}")
    print(f"{'='*80}\n")
    for r in resp.results:
        print(f"[{r.rank:>3}] {r.score:>5.1f}%  {r.match_level:<10}  {r.recommendation:<22}  {r.company_name}")
    s = resp.summary
    print(f"\nAvg: {s['avg_score']}%  |  Best: {s['highest_score']}%  |  Recommended: {s['recommended_count']}  |  Consider: {s['consider_count']}")
