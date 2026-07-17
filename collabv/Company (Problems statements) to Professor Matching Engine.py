"""
CollabV AI - Professor-Company Matching Engine v2
==================================================
3-tier weighted scoring with domain-department mapping:
  Tier 1 (55%): Keyword overlap + domain-dept alignment
  Tier 2 (40%): TF-IDF cosine similarity with rank normalization
  Tier 3 (5%):  Soft filters (location, collab type, seniority)
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.stats import rankdata
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


# ─── Data Structures ────────────────────────────────────────────────────────

@dataclass
class CompanyRequest:
    company_id: str
    company_name: str
    technical_area: List[str] = field(default_factory=list)
    industry: str = ""
    tech_stack: List[str] = field(default_factory=list)
    required_expertise: List[str] = field(default_factory=list)
    project_description: str = ""
    challenges: str = ""
    collaboration_type: str = ""
    location_preference: str = ""
    research_level: str = ""
    budget_tier: str = ""
    timeline_months: int = 0


@dataclass
class MatchResult:
    professor_name: str
    professor_id: str
    department: str
    score: float
    tier1_score: float
    tier2_score: float
    tier3_score: float
    reasons: List[str] = field(default_factory=list)
    contact: Dict = field(default_factory=dict)
    # ─── v3 factors ───
    patent_score: float = 0.0
    readiness_score: float = 0.0
    contextual_readiness: float = 0.0
    deal_probability: float = 0.0
    deal_band: str = ""
    explanation: Optional[Dict] = None
    # ─── v4 factors (per spec Layer 4) ───
    innovation_score: float = 0.0
    kg_domain_score: float = 0.0           # KG-derived domain overlap (0-100)
    innovation_bridges: List[str] = field(default_factory=list)

    # Spec-aligned aliases (computed properties)
    @property
    def skill_score(self) -> float:        # spec: skill similarity
        return self.tier2_score
    @property
    def domain_score(self) -> float:       # spec: domain overlap (KG-aware)
        return self.kg_domain_score if self.kg_domain_score else self.tier1_score
    @property
    def application_score(self) -> float:  # spec: application/industry match
        return self.tier1_score
    @property
    def experience_score(self) -> float:   # spec: experience strength
        return self.readiness_score
    @property
    def collab_readiness_score(self) -> float:
        return self.contextual_readiness or self.readiness_score


# ─── Domain-to-Department Mapping (Improvement 1) ───────────────────────────

# Maps industry/domain keywords to the departments most likely to help.
# Each entry is (keyword_patterns, list_of_matching_departments, boost_weight).
DOMAIN_DEPT_MAP = {
    "water": ["Ocean Engineering", "Civil Engineering", "Chemical Engineering"],
    "water treatment": ["Civil Engineering", "Chemical Engineering", "Ocean Engineering"],
    "water quality": ["Civil Engineering", "Chemical Engineering", "Ocean Engineering"],
    "wastewater": ["Civil Engineering", "Chemical Engineering", "Ocean Engineering"],
    "effluent": ["Civil Engineering", "Chemical Engineering"],
    "sewage": ["Civil Engineering", "Chemical Engineering"],
    "desalination": ["Chemical Engineering", "Ocean Engineering"],
    "aqua": ["Ocean Engineering", "Civil Engineering", "Chemical Engineering"],
    "watertech": ["Ocean Engineering", "Civil Engineering", "Chemical Engineering"],
    "environmentaltech": ["Civil Engineering", "Chemical Engineering"],
    "marinetech": ["Ocean Engineering", "Mechanical Engineering"],
    "ocean": ["Ocean Engineering", "Civil Engineering"],
    "marine": ["Ocean Engineering", "Mechanical Engineering"],
    "offshore": ["Ocean Engineering", "Civil Engineering"],
    "coastal": ["Ocean Engineering", "Civil Engineering"],
    "underwater": ["Ocean Engineering", "Electrical Engineering"],
    "pharma": ["Biotechnology", "Chemical Engineering", "Chemistry"],
    "pharmaceutical": ["Biotechnology", "Chemical Engineering", "Chemistry"],
    "drug": ["Biotechnology", "Chemical Engineering", "Chemistry"],
    "biotech": ["Biotechnology", "Chemical Engineering"],
    "healthcare": ["Biotechnology", "Applied Mechanics", "Electrical Engineering"],
    "medical": ["Biotechnology", "Applied Mechanics", "Engineering Design"],
    "biomedical": ["Applied Mechanics", "Biotechnology", "Engineering Design"],
    "enzyme": ["Biotechnology", "Chemical Engineering"],
    "protein": ["Biotechnology", "Chemistry"],
    "genomic": ["Biotechnology"],
    "fermentation": ["Biotechnology", "Chemical Engineering"],
    "energy": ["Electrical Engineering", "Mechanical Engineering", "Chemical Engineering"],
    "power": ["Electrical Engineering", "Mechanical Engineering"],
    "battery": ["Electrical Engineering", "Chemical Engineering", "Chemistry"],
    "solar": ["Electrical Engineering", "Physics", "Mechanical Engineering", "Chemical Engineering"],
    "renewable": ["Electrical Engineering", "Mechanical Engineering"],
    "grid": ["Electrical Engineering"],
    "cleantech": ["Chemical Engineering", "Electrical Engineering", "Mechanical Engineering", "Physics"],
    "semiconductor": ["Electrical Engineering", "Physics"],
    "vlsi": ["Electrical Engineering"],
    "electronics": ["Electrical Engineering", "Physics"],
    "wireless": ["Electrical Engineering"],
    "5g": ["Electrical Engineering"],
    "telecom": ["Electrical Engineering"],
    "radar": ["Electrical Engineering", "Aerospace Engineering"],
    "antenna": ["Electrical Engineering"],
    "iot": ["Electrical Engineering", "Computer Science & Engineering"],
    "ai": ["Computer Science & Engineering", "Electrical Engineering"],
    "machine learning": ["Computer Science & Engineering", "Electrical Engineering"],
    "deep learning": ["Computer Science & Engineering", "Electrical Engineering"],
    "nlp": ["Computer Science & Engineering"],
    "computer vision": ["Computer Science & Engineering", "Electrical Engineering"],
    "software": ["Computer Science & Engineering"],
    "data science": ["Computer Science & Engineering", "Mathematics"],
    "cybersecurity": ["Computer Science & Engineering"],
    "blockchain": ["Computer Science & Engineering"],
    "cloud": ["Computer Science & Engineering"],
    "robotics": ["Mechanical Engineering", "Aerospace Engineering", "Applied Mechanics", "Engineering Design", "Computer Science & Engineering"],
    "robot": ["Mechanical Engineering", "Aerospace Engineering", "Applied Mechanics", "Engineering Design"],
    "drone": ["Aerospace Engineering", "Mechanical Engineering", "Electrical Engineering"],
    "uav": ["Aerospace Engineering", "Mechanical Engineering"],
    "autonomous": ["Computer Science & Engineering", "Mechanical Engineering", "Electrical Engineering"],
    "aerospace": ["Aerospace Engineering", "Mechanical Engineering"],
    "aviation": ["Aerospace Engineering"],
    "satellite": ["Aerospace Engineering", "Electrical Engineering"],
    "defense": ["Aerospace Engineering", "Mechanical Engineering", "Electrical Engineering"],
    "defence": ["Aerospace Engineering", "Mechanical Engineering", "Electrical Engineering"],
    "steel": ["Metallurgical and Materials Engineering", "Mechanical Engineering"],
    "metal": ["Metallurgical and Materials Engineering", "Mechanical Engineering"],
    "alloy": ["Metallurgical and Materials Engineering"],
    "materials": ["Metallurgical and Materials Engineering", "Mechanical Engineering", "Physics"],
    "composite": ["Aerospace Engineering", "Metallurgical and Materials Engineering", "Mechanical Engineering"],
    "ceramic": ["Metallurgical and Materials Engineering", "Chemistry"],
    "corrosion": ["Metallurgical and Materials Engineering", "Chemical Engineering"],
    "manufacturing": ["Mechanical Engineering", "Metallurgical and Materials Engineering", "Engineering Design", "Applied Mechanics"],
    "additive manufacturing": ["Mechanical Engineering", "Metallurgical and Materials Engineering"],
    "3d printing": ["Mechanical Engineering", "Engineering Design"],
    "automotive": ["Mechanical Engineering", "Metallurgical and Materials Engineering", "Engineering Design"],
    "vehicle": ["Mechanical Engineering", "Engineering Design"],
    "combustion": ["Mechanical Engineering", "Aerospace Engineering"],
    "thermal": ["Mechanical Engineering", "Chemical Engineering"],
    "fluid": ["Mechanical Engineering", "Aerospace Engineering", "Chemical Engineering"],
    "cfd": ["Mechanical Engineering", "Aerospace Engineering"],
    "structural": ["Civil Engineering", "Aerospace Engineering", "Applied Mechanics"],
    "construction": ["Civil Engineering"],
    "infrastructure": ["Civil Engineering"],
    "concrete": ["Civil Engineering"],
    "bridge": ["Civil Engineering"],
    "seismic": ["Civil Engineering", "Applied Mechanics"],
    "earthquake": ["Civil Engineering", "Applied Mechanics"],
    "geotechnical": ["Civil Engineering"],
    "transportation": ["Civil Engineering"],
    "highway": ["Civil Engineering"],
    "catalyst": ["Chemical Engineering", "Chemistry"],
    "reaction": ["Chemical Engineering", "Chemistry"],
    "separation": ["Chemical Engineering"],
    "petroleum": ["Chemical Engineering", "Ocean Engineering"],
    "refinery": ["Chemical Engineering"],
    "electrochemistry": ["Chemical Engineering", "Chemistry"],
    "hydrogen": ["Chemical Engineering", "Mechanical Engineering"],
    "chemical": ["Chemical Engineering", "Chemistry"],
    "polymer": ["Chemistry", "Chemical Engineering", "Metallurgical and Materials Engineering"],
    "nano": ["Physics", "Chemistry", "Metallurgical and Materials Engineering"],
    "quantum": ["Physics", "Electrical Engineering"],
    "optics": ["Physics", "Electrical Engineering"],
    "photonics": ["Physics", "Electrical Engineering"],
    "fintech": ["Management Studies", "Computer Science & Engineering", "Mathematics"],
    "management": ["Management Studies"],
    "supply chain": ["Management Studies", "Mechanical Engineering"],
    "operations": ["Management Studies", "Mechanical Engineering"],
    "finance": ["Management Studies", "Mathematics"],
    "optimization": ["Mathematics", "Computer Science & Engineering", "Mechanical Engineering"],
    "cryptography": ["Mathematics", "Computer Science & Engineering"],
    "statistics": ["Mathematics", "Computer Science & Engineering"],
    "rehabilitation": ["Applied Mechanics", "Engineering Design", "Biotechnology"],
    "prosthetics": ["Engineering Design", "Applied Mechanics"],
    "wearable": ["Electrical Engineering", "Engineering Design"],
    "sensor": ["Electrical Engineering", "Mechanical Engineering", "Physics"],
    "environment": ["Civil Engineering", "Chemical Engineering"],
    "pollution": ["Civil Engineering", "Chemical Engineering"],
    "climate": ["Civil Engineering", "Ocean Engineering"],
    "sustainability": ["Civil Engineering", "Chemical Engineering", "Mechanical Engineering"],
    "agritech": ["Biotechnology", "Chemical Engineering", "Civil Engineering"],
    "agriculture": ["Biotechnology", "Chemical Engineering", "Civil Engineering"],
    "crop": ["Biotechnology", "Chemical Engineering"],
    "food": ["Biotechnology", "Chemical Engineering"],
    "food processing": ["Chemical Engineering", "Biotechnology"],
    "nutrition": ["Biotechnology", "Chemical Engineering"],
    "soil": ["Civil Engineering", "Chemical Engineering", "Biotechnology"],
    "irrigation": ["Civil Engineering", "Ocean Engineering"],
    "textile": ["Mechanical Engineering", "Chemical Engineering", "Engineering Design"],
    "logistics": ["Management Studies", "Mechanical Engineering", "Computer Science & Engineering"],
    "supply chain": ["Management Studies", "Mechanical Engineering"],
    "hr": ["Management Studies", "Computer Science & Engineering"],
    "recruitment": ["Management Studies", "Computer Science & Engineering"],
    "retail": ["Management Studies", "Computer Science & Engineering"],
    "ecommerce": ["Computer Science & Engineering", "Management Studies"],
    "travel": ["Management Studies", "Computer Science & Engineering"],
    "hotel": ["Management Studies", "Computer Science & Engineering"],
    "legal": ["Management Studies", "Computer Science & Engineering"],
    "education": ["Computer Science & Engineering", "Management Studies", "Humanities and Social Science"],
    "edtech": ["Computer Science & Engineering", "Electrical Engineering"],
    "sport": ["Applied Mechanics", "Engineering Design", "Computer Science & Engineering"],
    "urban": ["Civil Engineering", "Management Studies", "Computer Science & Engineering"],
    "smart city": ["Civil Engineering", "Electrical Engineering", "Computer Science & Engineering"],
    "real estate": ["Civil Engineering", "Management Studies"],
    "property": ["Civil Engineering", "Management Studies"],
    "wellness": ["Biotechnology", "Applied Mechanics", "Electrical Engineering"],
    "mental health": ["Biotechnology", "Computer Science & Engineering"],
    "physio": ["Applied Mechanics", "Biotechnology", "Engineering Design"],
    "accessibility": ["Engineering Design", "Applied Mechanics", "Computer Science & Engineering"],
    "disability": ["Engineering Design", "Applied Mechanics"],
    "govtech": ["Computer Science & Engineering", "Management Studies"],
    "tax": ["Management Studies", "Computer Science & Engineering", "Mathematics"],
    "insurance": ["Management Studies", "Mathematics", "Computer Science & Engineering"],
    "banking": ["Management Studies", "Mathematics", "Computer Science & Engineering"],
}


def _get_dept_boost(request_text: str, prof_dept_short: str) -> float:
    """Return a boost score [0, 1] if the request text maps to the professor's department."""
    text_lower = request_text.lower()
    best_boost = 0.0
    for keyword, depts in DOMAIN_DEPT_MAP.items():
        if keyword in text_lower:
            for rank, dept in enumerate(depts):
                if dept.lower() in prof_dept_short.lower() or prof_dept_short.lower() in dept.lower():
                    # First-listed dept gets full boost, others get less
                    boost = 1.0 - (rank * 0.2)
                    best_boost = max(best_boost, boost)
    return min(best_boost, 1.0)


# ─── Matching Engine ────────────────────────────────────────────────────────

class MatchingEngine:
    def __init__(self, professors_path: str = None, enable_embeddings: bool = True):
        if professors_path is None:
            # Try patent-enriched first, then NLP-enriched, then plain enriched
            base = Path(__file__).parent.parent
            for candidate in ("iitm_professors_with_patents.json",
                              "iitm_professors_nlp.json",
                              "iitm_professors_enriched.json"):
                p = base / candidate
                if p.exists():
                    professors_path = str(p)
                    break
        try:
            with open(professors_path, encoding="utf-8") as f:
                self.professors = json.load(f)
        except (FileNotFoundError, TypeError):
            # Live-data-only mode: no professors file resolved (or none of the
            # candidates above exist) - start with zero professors rather than
            # crashing the app. The only source of professors is then whatever
            # gets registered live via POST /professor/profile.
            self.professors = []
        self._build_profiles()

        # ─── Optional v3/v4 components (graceful fallback) ───
        self.patent_scorer = None
        self.readiness_predictor = None
        self.embedding_engine = None
        self.innovation_scorer = None
        self.knowledge_graph = None
        self.factor_weights: Dict[str, float] = {}

        try:
            from .patent_scorer import PatentScorer
            self.patent_scorer = PatentScorer()
        except Exception as e:
            print(f"[engine] Patent scorer disabled: {e}")

        try:
            from .collab_readiness import CollabReadinessPredictor
            self.readiness_predictor = CollabReadinessPredictor()
        except Exception as e:
            print(f"[engine] Readiness predictor disabled: {e}")

        try:
            from .innovation_scorer import InnovationScorer
            self.innovation_scorer = InnovationScorer()
        except Exception as e:
            print(f"[engine] Innovation scorer disabled: {e}")

        try:
            from .knowledge_graph import KnowledgeGraph
            self.knowledge_graph = KnowledgeGraph(self.professors).build()
            print(f"[engine] Knowledge graph ready: {self.knowledge_graph.stats()}")
        except Exception as e:
            print(f"[engine] Knowledge graph disabled: {e}")
            self.knowledge_graph = None

        try:
            from .retrainer import load_weights
            self.factor_weights = load_weights()
        except Exception:
            self.factor_weights = {
                "tier1_score": 0.40, "tier2_score": 0.25, "tier3_score": 0.05,
                "patent_score": 0.10, "readiness_score": 0.10,
                "innovation_score": 0.10,
            }
        # Always ensure the new factor has a default weight even when an old
        # collabv_weights.json is on disk.
        self.factor_weights.setdefault("innovation_score", 0.05)

        if enable_embeddings:
            self._init_embeddings()

    def _init_embeddings(self) -> None:
        try:
            from .embeddings import EmbeddingEngine
            ee = EmbeddingEngine()
            if not ee.is_ready:
                self.embedding_engine = None
                return
            # Try to load a pre-built index
            index_path = Path(__file__).parent.parent / "collabv_embeddings.index"
            loaded = ee.load_index(str(index_path))
            if not loaded:
                print("[engine] Building embedding index (first run)...")
                ee.build_professor_index(self.professors)
                try:
                    ee.save_index(str(index_path))
                except Exception as e:
                    print(f"[engine] Could not save embedding index: {e}")
            self.embedding_engine = ee
            print(f"[engine] Embedding engine ready ({len(ee.prof_ids)} profs)")
        except Exception as e:
            print(f"[engine] Embeddings disabled: {e}")
            self.embedding_engine = None

    def _build_profiles(self):
        """Pre-compute enriched text profiles for TF-IDF (Improvement 3)."""
        self.profiles = []
        for p in self.professors:
            parts = []
            tags = p.get("matching_tags", {})

            # Research interests weighted 3x
            research = (
                p.get("research_areas", [])
                + tags.get("research_domain_tags", [])
                + [tags.get("primary_domain", "")]
            )
            for _ in range(3):
                parts.extend(research)

            # Technical expertise weighted 2x
            expertise = (
                p.get("technical_expertise", [])
                + tags.get("tech_skill_tags", [])
            )
            for _ in range(2):
                parts.extend(expertise)

            # First 3 publication titles (full text)
            pubs = p.get("publications", [])[:3]
            parts.extend(pubs)

            # Department name (helps TF-IDF pick up dept-specific terms)
            dept = p.get("department", "")
            parts.append(dept)
            parts.append(dept.replace("Department of ", ""))

            # Biography
            parts.append(p.get("biography", ""))

            # Industry tags
            parts.extend(tags.get("industry_tags", []))

            # NLP-enriched fields weighted 2x (if available from profile_nlp.py)
            nlp_tags = p.get("nlp_tags", [])
            for _ in range(2):
                parts.extend(nlp_tags)
            parts.append(p.get("expertise_summary", ""))

            # Add top domain names to profile text
            for domain_name in list(p.get("domain_scores", {}).keys())[:5]:
                parts.append(domain_name)

            self.profiles.append(" ".join(str(x) for x in parts if x))

    # ─── Helpers ─────────────────────────────────────────────────────────────

    def _normalize_tokens(self, items: List[str]) -> set:
        tokens = set()
        for item in items:
            # Keep multi-word phrases as well as individual words
            item_lower = str(item).lower().strip()
            if len(item_lower) > 2:
                tokens.add(item_lower)
            for word in re.split(r"[\s,/\-\(\)]+", item_lower):
                word = word.strip(".")
                if len(word) > 2:
                    tokens.add(word)
        return tokens

    def _keyword_overlap(self, set_a: set, set_b: set) -> float:
        if not set_a or not set_b:
            return 0.0
        intersection = set_a & set_b
        # Use Jaccard-like but favor recall over precision
        return len(intersection) / max(len(set_a), 1)

    # ─── Tier 1: Keyword + Domain-Dept Alignment (55%) ──────────────────────

    def _tier1_score(self, request: CompanyRequest, prof: dict) -> Tuple[float, List[str]]:
        tags = prof.get("matching_tags", {})
        dept_short = prof.get("department", "").replace("Department of ", "")
        reasons = []

        # 1a. technical_area vs research interests (30%)
        req_tech = self._normalize_tokens(request.technical_area + request.required_expertise)
        prof_research = self._normalize_tokens(
            prof.get("research_areas", [])
            + tags.get("research_domain_tags", [])
            + [tags.get("primary_domain", "")]
            + prof.get("nlp_tags", [])  # NLP-enriched tags
        )
        tech_score = self._keyword_overlap(req_tech, prof_research)
        matched_tech = req_tech & prof_research
        if matched_tech:
            display = sorted(matched_tech, key=len, reverse=True)[:5]
            reasons.append(f"Research match: {', '.join(display)}")

        # 1b. Domain-department alignment (20%)
        # Combine all request text for domain matching
        request_text = " ".join([
            request.industry,
            request.project_description,
            request.challenges,
            " ".join(request.technical_area),
            " ".join(request.required_expertise),
        ])
        dept_boost = _get_dept_boost(request_text, dept_short)

        # Also check industry tag overlap
        req_industry = self._normalize_tokens([request.industry] + request.technical_area)
        prof_industry = self._normalize_tokens(
            tags.get("industry_tags", [])
            + [dept_short]
            + prof.get("industry_exposure", [])
        )
        ind_overlap = self._keyword_overlap(req_industry, prof_industry)

        # Combine: domain mapping is primary, tag overlap is secondary
        dept_score = max(dept_boost * 0.7 + ind_overlap * 0.3, ind_overlap)
        if dept_boost > 0.5:
            reasons.append(f"Department fit: {dept_short}")
        elif req_industry & prof_industry:
            reasons.append(f"Industry alignment: {dept_short}")

        # NLP industry fit bonus (if available)
        industry_fit = prof.get("industry_fit", {})
        nlp_bonus = 0.0
        if industry_fit:
            search_terms = [request.industry.lower()]
            for ta in request.technical_area:
                search_terms.append(ta.lower())
            best_fit = 0.0
            for ind_key, fit_score in industry_fit.items():
                ind_lower = ind_key.lower()
                for term in search_terms:
                    if not term:
                        continue
                    term_words = set(term.split())
                    ind_words = set(ind_lower.replace("&", "").split())
                    if term_words & ind_words or term in ind_lower or ind_lower in term:
                        best_fit = max(best_fit, fit_score)
            nlp_bonus = best_fit * 0.10

        # Weights: research 30%, dept alignment 25%, NLP bonus up to 10%
        weighted = tech_score * 0.30 + dept_score * 0.25 + nlp_bonus
        return weighted, reasons

    # ─── Tier 2: Dense embeddings (preferred) or TF-IDF fallback ────────────

    def _tier2_scores(self, request: CompanyRequest) -> np.ndarray:
        if self.embedding_engine is not None and self.embedding_engine.has_index:
            try:
                from .embeddings import EmbeddingEngine
                query_text = EmbeddingEngine.request_text(request)
                q_emb = self.embedding_engine.encode(query_text)
                all_scores = self.embedding_engine.score_all(q_emb)
                # Map prof_id-aligned scores back into our professor order
                id_to_score = dict(zip(self.embedding_engine.prof_ids, all_scores))
                ordered = np.array([
                    id_to_score.get(str(p.get("professor_id", "")), 0.0)
                    for p in self.professors
                ], dtype="float32")
                # Cosine similarity is in [-1, 1] (mostly [0, 1] for our data).
                # Apply same shape boost as TF-IDF path.
                ordered = np.clip(ordered, 0, 1)
                ordered = np.power(ordered, 0.7)
                # Scale to the same ~0-0.45 weight contribution
                return ordered * 0.45
            except Exception as e:
                print(f"[engine] Embedding path failed, falling back to TF-IDF: {e}")
        return self._tier2_scores_tfidf(request)

    def _tier2_scores_tfidf(self, request: CompanyRequest) -> np.ndarray:
        expertise_query = " ".join(request.required_expertise + request.technical_area)
        project_query = request.project_description
        challenges_query = request.challenges

        all_docs = self.profiles + [expertise_query, project_query, challenges_query]

        vectorizer = TfidfVectorizer(
            max_features=8000,
            stop_words="english",
            ngram_range=(1, 2),
            min_df=1,
            sublinear_tf=True,
        )
        tfidf_matrix = vectorizer.fit_transform(all_docs)

        n_profs = len(self.professors)
        prof_vectors = tfidf_matrix[:n_profs]
        expertise_vec = tfidf_matrix[n_profs]
        project_vec = tfidf_matrix[n_profs + 1]
        challenges_vec = tfidf_matrix[n_profs + 2]

        sim_expertise = cosine_similarity(prof_vectors, expertise_vec.reshape(1, -1)).flatten()
        sim_project = cosine_similarity(prof_vectors, project_vec.reshape(1, -1)).flatten()
        sim_challenges = cosine_similarity(prof_vectors, challenges_vec.reshape(1, -1)).flatten()

        def rank_normalize(arr):
            if arr.max() == arr.min():
                return np.zeros_like(arr)
            ranks = rankdata(arr, method="average")
            return (ranks - 1) / max(len(ranks) - 1, 1)

        norm_expertise = rank_normalize(sim_expertise)
        norm_project = rank_normalize(sim_project)
        norm_challenges = rank_normalize(sim_challenges)

        # Score amplification: spread the 0.5-0.8 range to 0.3-0.9
        norm_expertise = np.power(norm_expertise, 0.7)
        norm_project = np.power(norm_project, 0.7)
        norm_challenges = np.power(norm_challenges, 0.7)

        # Weights: expertise 30%, project 10%, challenges 5%
        weighted = norm_expertise * 0.30 + norm_project * 0.10 + norm_challenges * 0.05
        return weighted

    # ─── Tier 3: Soft Filters (5%) ──────────────────────────────────────────

    def _tier3_score(self, request: CompanyRequest, prof: dict) -> float:
        tags = prof.get("matching_tags", {})
        score = 0.0

        # 3a. Location boost (2%)
        loc_pref = request.location_preference.lower()
        if not loc_pref or loc_pref == "any":
            score += 0.02
        elif any(k in loc_pref for k in ("chennai", "tamil nadu", "south india")):
            score += 0.02

        # 3b. Collaboration type fit (2%)
        req_collab = request.collaboration_type.lower()
        prof_collab = [c.lower() for c in tags.get("collab_type_tags", [])]
        if not req_collab:
            score += 0.02
        elif any(req_collab in c or c in req_collab for c in prof_collab):
            score += 0.02
        elif prof_collab:
            score += 0.01

        # 3c. Research level / seniority (1%)
        req_level = request.research_level.lower()
        prof_levels = [l.lower() for l in tags.get("research_level_tags", [])]
        if not req_level:
            score += 0.01
        elif any(req_level in l for l in prof_levels):
            score += 0.01

        return score

    # ─── Main Match ──────────────────────────────────────────────────────────

    def match(self, request: CompanyRequest, top_k: int = 10) -> List[MatchResult]:
        tier2_scores = self._tier2_scores(request)
        w = self.factor_weights

        # The internal tier scores have their own weighting baked in:
        #   t1 max ~ 0.65 (research 0.30 + dept 0.25 + nlp bonus 0.10)
        #   t2 max ~ 0.45 (expertise 0.30 + project 0.10 + challenges 0.05)
        #   t3 max ~ 0.05
        # We rescale each to a true 0-100 score so the trained weights work
        # cleanly as a weighted average across all 5 factors.
        T1_MAX, T2_MAX, T3_MAX = 0.65, 0.45, 0.05

        results = []
        for i, prof in enumerate(self.professors):
            t1_score, reasons = self._tier1_score(request, prof)
            t2_score = tier2_scores[i]
            t3_score = self._tier3_score(request, prof)

            # ─── New v3 factors (0-100 each) ───
            patent_score = 0.0
            if self.patent_scorer is not None:
                try:
                    portfolio = self.patent_scorer.score_portfolio(prof)
                    relevance = self.patent_scorer.score_relevance(prof, request)
                    patent_score = portfolio.total_score * 0.5 + relevance.relevance_score * 0.5
                    if relevance.matching_keywords:
                        reasons.append(
                            "Patent overlap: " + ", ".join(relevance.matching_keywords[:3])
                        )
                except Exception as e:
                    print(f"[engine] Patent scoring failed for {prof.get('name')}: {e}")

            readiness_score = 0.0
            contextual_readiness_val = 0.0
            if self.readiness_predictor is not None:
                try:
                    contextual = self.readiness_predictor.predict_readiness_for_request(prof, request)
                    readiness_score = contextual.base_readiness.overall_score
                    contextual_readiness_val = contextual.contextual_score
                    if contextual.contextual_drivers:
                        reasons.append(contextual.contextual_drivers[0])
                except Exception as e:
                    print(f"[engine] Readiness scoring failed for {prof.get('name')}: {e}")

            # ─── Innovation scoring (v4 - the 6th spec factor) ───
            innovation_score = 0.0
            innovation_bridges: List[str] = []
            if self.innovation_scorer is not None:
                try:
                    ino = self.innovation_scorer.score(prof, request)
                    innovation_score = ino.total_score
                    innovation_bridges = ino.bridges_detected
                    if ino.bridges_detected and ino.total_score >= 75:
                        reasons.append(
                            f"Cross-domain bridge: {ino.bridges_detected[0]}"
                        )
                except Exception as e:
                    print(f"[engine] Innovation scoring failed for {prof.get('name')}: {e}")

            # ─── Knowledge-graph domain overlap ───
            kg_domain_score = 0.0
            if self.knowledge_graph is not None:
                try:
                    from .innovation_scorer import _classify_text
                    req_text = " ".join([
                        str(request.industry or ""),
                        " ".join(request.technical_area or []),
                        " ".join(request.required_expertise or []),
                        str(request.project_description or ""),
                    ])
                    req_domains = list(set(_classify_text(req_text)))
                    if req_domains:
                        kg_overlap = self.knowledge_graph.domain_overlap_score(
                            prof.get("professor_id", ""), req_domains,
                        )
                        kg_domain_score = kg_overlap * 100
                except Exception as e:
                    print(f"[engine] KG scoring failed for {prof.get('name')}: {e}")

            # Rescale tier scores so each is a true 0-100 score, then
            # weighted-average with trained weights.
            t1_pct = min(t1_score / T1_MAX, 1.0) * 100
            t2_pct = min(t2_score / T2_MAX, 1.0) * 100
            t3_pct = min(t3_score / T3_MAX, 1.0) * 100
            final_score = (
                t1_pct * w.get("tier1_score", 0.40)
                + t2_pct * w.get("tier2_score", 0.25)
                + t3_pct * w.get("tier3_score", 0.05)
                + patent_score * w.get("patent_score", 0.10)
                + readiness_score * w.get("readiness_score", 0.10)
                + innovation_score * w.get("innovation_score", 0.10)
            )
            final_score = round(min(final_score, 100), 1)

            results.append(MatchResult(
                professor_name=prof["name"],
                professor_id=prof.get("professor_id", ""),
                department=prof.get("department", "").replace("Department of ", ""),
                score=final_score,
                tier1_score=round(t1_pct, 1),
                tier2_score=round(t2_pct, 1),
                tier3_score=round(t3_pct, 1),
                patent_score=round(patent_score, 1),
                readiness_score=round(readiness_score, 1),
                contextual_readiness=round(contextual_readiness_val, 1),
                innovation_score=round(innovation_score, 1),
                kg_domain_score=round(kg_domain_score, 1),
                innovation_bridges=innovation_bridges,
                reasons=reasons,
                contact=prof.get("contact") if isinstance(prof.get("contact"), dict) else {},
            ))

        results.sort(key=lambda r: r.score, reverse=True)

        # Diversity boost: if top 3 are all same dept, penalize to encourage variety
        top = results[:max(top_k, 10)]
        if len(top) >= 3 and top[0].department == top[1].department == top[2].department:
            dominant_dept = top[0].department
            penalty_count = 0
            for r in top:
                if r.department == dominant_dept and penalty_count < 3:
                    r.score = round(r.score * 0.95, 1)
                    r.tier1_score = round(r.tier1_score * 0.95, 1)
                    penalty_count += 1
            top.sort(key=lambda r: r.score, reverse=True)

        return top[:top_k]

    def get_professors(self, department: Optional[str] = None) -> List[dict]:
        if department:
            return [p for p in self.professors if department.lower() in p.get("department", "").lower()]
        return self.professors
