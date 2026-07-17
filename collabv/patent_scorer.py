"""
CollabV AI - Model 3: Patent Valuation Scoring
================================================
Evaluates a professor's patent portfolio and the relevance of those patents
to a specific company request.

Public API:
    PatentScorer().score_portfolio(prof) -> PatentPortfolioScore
    PatentScorer().score_relevance(prof, company_request) -> PatentRelevanceScore
    PatentScorer().get_patent_insights(prof) -> dict
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Config ─────────────────────────────────────────────────────────────────

@dataclass
class PatentScoringConfig:
    """All scoring weights and thresholds. Tweak these without touching code."""

    # Portfolio sub-weights (must sum to 1.0)
    count_weight: float = 0.30
    recency_weight: float = 0.25
    status_weight: float = 0.15
    diversity_weight: float = 0.15
    collaboration_weight: float = 0.15

    # Count scoring - diminishing returns
    count_first_tier_cap: int = 5      # First 5 patents weighted most
    count_first_tier_value: float = 12.0  # per patent in first tier
    count_second_tier_cap: int = 15    # Patents 6-15
    count_second_tier_value: float = 4.0  # per patent in second tier
    count_third_tier_value: float = 1.5   # per patent above 15

    # Recency time-decay (years)
    recency_recent_max: int = 3
    recency_mid_max: int = 5
    recency_old_max: int = 10
    recency_recent_factor: float = 1.0
    recency_mid_factor: float = 0.7
    recency_old_factor: float = 0.4
    recency_very_old_factor: float = 0.2

    # Status multipliers
    status_granted: float = 1.0
    status_published: float = 0.7
    status_filed: float = 0.5

    # Relevance scoring
    relevance_kw_weight: float = 0.55
    relevance_tfidf_weight: float = 0.30
    relevance_domain_weight: float = 0.15

    # No-patent baseline
    no_patent_baseline: float = 25.0


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class PatentPortfolioScore:
    total_score: float
    count_score: float
    recency_score: float
    status_score: float
    diversity_score: float
    collaboration_score: float
    patent_count: int
    newest_patent_year: Optional[int]
    top_domains: List[str] = field(default_factory=list)
    has_patents: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PatentRelevanceScore:
    relevance_score: float
    matching_keywords: List[str] = field(default_factory=list)
    matching_domains: List[str] = field(default_factory=list)
    matched_patents: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Helpers ────────────────────────────────────────────────────────────────

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "ai_ml": ["machine learning", "deep learning", "neural", "ai ", "artificial intelligence", "nlp", "cnn", "rnn", "transformer"],
    "energy": ["solar", "battery", "fuel cell", "renewable", "hydrogen", "photovoltaic", "wind", "energy storage"],
    "materials": ["alloy", "composite", "polymer", "ceramic", "nanomaterial", "graphene", "metallurgy"],
    "biotech": ["enzyme", "protein", "biopolymer", "drug delivery", "biomarker", "vaccine", "genom"],
    "chemicals": ["catalyst", "reaction", "synthesis", "polymerization", "petrochemical"],
    "electronics": ["semiconductor", "vlsi", "circuit", "antenna", "5g", "rf", "mems"],
    "aerospace": ["aerodynamic", "uav", "drone", "satellite", "propulsion", "spacecraft"],
    "mechanical": ["mechanism", "linkage", "actuator", "manufacturing", "machining", "additive"],
    "civil": ["concrete", "structural", "seismic", "geotechnical", "pavement", "construction"],
    "chemical_eng": ["distillation", "separation", "membrane", "reactor", "process engineering"],
    "robotics": ["robot", "manipulator", "gripper", "autonomous", "robotic"],
    "healthcare": ["medical device", "diagnostic", "prosthet", "implant", "surgical"],
    "sensors": ["sensor", "transducer", "wearable", "iot device"],
    "optics": ["optical", "photonic", "laser", "lens", "fiber optic"],
}

_DATE_RE = re.compile(r"(\d{4})")


def _extract_year(value: Any) -> Optional[int]:
    """Extract a 4-digit year from any value (string, int, datetime)."""
    if value is None:
        return None
    if isinstance(value, int) and 1900 <= value <= 2100:
        return value
    if isinstance(value, datetime):
        return value.year
    text = str(value)
    match = _DATE_RE.search(text)
    if match:
        year = int(match.group(1))
        if 1900 <= year <= 2100:
            return year
    return None


def _classify_domain(text: str) -> List[str]:
    """Return list of domain keys that appear in text."""
    text_lower = text.lower()
    hits = []
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                hits.append(domain)
                break
    return hits


def _patent_text(patent: Dict[str, Any]) -> str:
    """Combine all searchable text fields from a patent record."""
    parts = [
        str(patent.get("title", "")),
        str(patent.get("abstract", "")),
        str(patent.get("description", "")),
    ]
    return " ".join(p for p in parts if p)


def _patent_status(patent: Dict[str, Any]) -> str:
    """Normalize the patent status into one of {granted, published, filed}."""
    raw = str(patent.get("status", "")).lower()
    if "grant" in raw:
        return "granted"
    if "publish" in raw:
        return "published"
    return "filed"


def _tokenize(text: str) -> set:
    """Coarse tokenizer: lowercase words with len > 2."""
    tokens = set()
    for word in re.split(r"[\s,/\-\(\)\.;:]+", text.lower()):
        word = word.strip()
        if len(word) > 2 and not word.isdigit():
            tokens.add(word)
    return tokens


# ─── Scorer ─────────────────────────────────────────────────────────────────

class PatentScorer:
    """Score professors' patent portfolios and their relevance to requests."""

    def __init__(self, config: Optional[PatentScoringConfig] = None) -> None:
        self.config = config or PatentScoringConfig()
        self.current_year = datetime.now().year

    # ─── Portfolio scoring ──────────────────────────────────────────────────

    def score_portfolio(self, professor: Dict[str, Any]) -> PatentPortfolioScore:
        """Evaluate the full patent portfolio of a professor."""
        patents = self._extract_patents(professor)
        if not patents:
            return PatentPortfolioScore(
                total_score=self.config.no_patent_baseline,
                count_score=0.0,
                recency_score=0.0,
                status_score=0.0,
                diversity_score=0.0,
                collaboration_score=0.0,
                patent_count=0,
                newest_patent_year=None,
                top_domains=[],
                has_patents=False,
            )

        count_score = self._score_count(len(patents))
        recency_score, newest_year = self._score_recency(patents)
        status_score = self._score_status(patents)
        diversity_score, top_domains = self._score_diversity(patents)
        collab_score = self._score_collaboration(patents)

        cfg = self.config
        total = (
            count_score * cfg.count_weight
            + recency_score * cfg.recency_weight
            + status_score * cfg.status_weight
            + diversity_score * cfg.diversity_weight
            + collab_score * cfg.collaboration_weight
        )

        return PatentPortfolioScore(
            total_score=round(min(total, 100.0), 1),
            count_score=round(count_score, 1),
            recency_score=round(recency_score, 1),
            status_score=round(status_score, 1),
            diversity_score=round(diversity_score, 1),
            collaboration_score=round(collab_score, 1),
            patent_count=len(patents),
            newest_patent_year=newest_year,
            top_domains=top_domains,
            has_patents=True,
        )

    # ─── Relevance scoring ──────────────────────────────────────────────────

    def score_relevance(self, professor: Dict[str, Any], company_request: Any) -> PatentRelevanceScore:
        """Score how well a professor's patents match a company request."""
        patents = self._extract_patents(professor)
        if not patents:
            return PatentRelevanceScore(relevance_score=0.0)

        request_text = self._request_text(company_request)
        request_tokens = _tokenize(request_text)
        request_domains = set(_classify_domain(request_text))

        kw_matches: set = set()
        domain_matches: set = set()
        matched_patent_titles: List[str] = []

        for patent in patents:
            patent_text = _patent_text(patent)
            patent_tokens = _tokenize(patent_text)
            patent_domains = set(_classify_domain(patent_text))

            overlap = request_tokens & patent_tokens
            if overlap:
                kw_matches |= overlap
                title = str(patent.get("title", "(untitled patent)")).strip()
                if title and title not in matched_patent_titles:
                    matched_patent_titles.append(title)

            domain_matches |= request_domains & patent_domains

        # Per-component scores (each 0-100)
        kw_density = 0.0
        if request_tokens:
            kw_density = min(len(kw_matches) / max(len(request_tokens), 1) * 200, 100)

        tfidf_proxy = self._tfidf_overlap_score(request_tokens, patents)
        domain_score = 100.0 if domain_matches else 0.0
        if request_domains and not domain_matches:
            domain_score = 0.0
        elif not request_domains:
            domain_score = 50.0  # request has no clear domain; neutral

        cfg = self.config
        relevance = (
            kw_density * cfg.relevance_kw_weight
            + tfidf_proxy * cfg.relevance_tfidf_weight
            + domain_score * cfg.relevance_domain_weight
        )

        return PatentRelevanceScore(
            relevance_score=round(min(relevance, 100.0), 1),
            matching_keywords=sorted(kw_matches, key=len, reverse=True)[:10],
            matching_domains=sorted(domain_matches),
            matched_patents=matched_patent_titles[:5],
        )

    # ─── Public insights ────────────────────────────────────────────────────

    def get_patent_insights(self, professor: Dict[str, Any]) -> Dict[str, Any]:
        """Return a structured insight bundle for a professor's patent activity."""
        portfolio = self.score_portfolio(professor)
        patents = self._extract_patents(professor)

        by_status: Dict[str, int] = {"granted": 0, "published": 0, "filed": 0}
        by_year: Dict[int, int] = {}
        co_inventors: set = set()

        for p in patents:
            by_status[_patent_status(p)] += 1
            year = _extract_year(p.get("filing_date") or p.get("year"))
            if year:
                by_year[year] = by_year.get(year, 0) + 1
            for inv in self._extract_co_inventors(p, professor.get("name", "")):
                co_inventors.add(inv)

        return {
            "portfolio": portfolio.to_dict(),
            "by_status": by_status,
            "by_year": dict(sorted(by_year.items(), reverse=True)),
            "co_inventor_count": len(co_inventors),
            "top_co_inventors": sorted(co_inventors)[:8],
            "recent_patents": self._recent_patent_summaries(patents, limit=5),
        }

    # ─── Internal scoring helpers ───────────────────────────────────────────

    def _score_count(self, n: int) -> float:
        """Diminishing returns count score, capped at 100."""
        cfg = self.config
        score = 0.0
        if n <= 0:
            return 0.0

        first = min(n, cfg.count_first_tier_cap)
        score += first * cfg.count_first_tier_value

        if n > cfg.count_first_tier_cap:
            second = min(n - cfg.count_first_tier_cap, cfg.count_second_tier_cap - cfg.count_first_tier_cap)
            score += second * cfg.count_second_tier_value

        if n > cfg.count_second_tier_cap:
            third = n - cfg.count_second_tier_cap
            score += third * cfg.count_third_tier_value

        return min(score, 100.0)

    def _score_recency(self, patents: List[Dict[str, Any]]) -> Tuple[float, Optional[int]]:
        cfg = self.config
        total_weight = 0.0
        contributions: List[float] = []
        newest_year: Optional[int] = None

        for p in patents:
            year = _extract_year(p.get("filing_date") or p.get("year"))
            if not year:
                # No date - assume mid recency
                contributions.append(cfg.recency_mid_factor)
                continue
            if newest_year is None or year > newest_year:
                newest_year = year
            age = self.current_year - year
            if age <= cfg.recency_recent_max:
                factor = cfg.recency_recent_factor
            elif age <= cfg.recency_mid_max:
                factor = cfg.recency_mid_factor
            elif age <= cfg.recency_old_max:
                factor = cfg.recency_old_factor
            else:
                factor = cfg.recency_very_old_factor
            contributions.append(factor)

        if not contributions:
            return 0.0, newest_year
        # Weighted average, scaled to 100
        score = sum(contributions) / len(contributions) * 100
        # Boost if at least one is recent
        if any(c >= cfg.recency_recent_factor for c in contributions):
            score = min(score * 1.15, 100.0)
        return score, newest_year

    def _score_status(self, patents: List[Dict[str, Any]]) -> float:
        cfg = self.config
        factors = []
        for p in patents:
            status = _patent_status(p)
            if status == "granted":
                factors.append(cfg.status_granted)
            elif status == "published":
                factors.append(cfg.status_published)
            else:
                factors.append(cfg.status_filed)
        if not factors:
            return 0.0
        return sum(factors) / len(factors) * 100

    def _score_diversity(self, patents: List[Dict[str, Any]]) -> Tuple[float, List[str]]:
        domain_counts: Dict[str, int] = {}
        for p in patents:
            text = _patent_text(p)
            for d in _classify_domain(text):
                domain_counts[d] = domain_counts.get(d, 0) + 1

        if not domain_counts:
            return 30.0, []  # neutral - couldn't classify

        n_domains = len(domain_counts)
        # Diversity score: more domains = better, but capped
        # 1 domain: 40, 2: 65, 3: 80, 4+: 95
        if n_domains == 1:
            score = 40.0
        elif n_domains == 2:
            score = 65.0
        elif n_domains == 3:
            score = 80.0
        else:
            score = min(80 + (n_domains - 3) * 5, 100)

        top_domains = sorted(domain_counts, key=lambda k: -domain_counts[k])[:5]
        return score, top_domains

    def _score_collaboration(self, patents: List[Dict[str, Any]]) -> float:
        co_inventors: set = set()
        for p in patents:
            for inv in self._extract_co_inventors(p, ""):
                co_inventors.add(inv.lower().strip())

        n = len(co_inventors)
        if n == 0:
            return 20.0
        if n <= 3:
            return 40.0 + n * 10
        if n <= 8:
            return 70.0 + (n - 3) * 4
        return min(90.0 + (n - 8) * 1.5, 100.0)

    # ─── TF-IDF proxy without re-vectorizing ───────────────────────────────

    def _tfidf_overlap_score(self, request_tokens: set, patents: List[Dict[str, Any]]) -> float:
        """Lightweight proxy: count weighted token overlap across all patent text.

        Uses inverse-frequency weighting so common words contribute less. Good
        enough to differentiate "exact-topic" patents from incidental keyword hits
        without spinning up a full vectorizer.
        """
        if not request_tokens or not patents:
            return 0.0

        all_text = " ".join(_patent_text(p) for p in patents)
        all_tokens = _tokenize(all_text)
        if not all_tokens:
            return 0.0

        # Document frequency proxy: how many patents contain each request token
        token_df: Dict[str, int] = {}
        for p in patents:
            patent_tokens = _tokenize(_patent_text(p))
            for tok in request_tokens & patent_tokens:
                token_df[tok] = token_df.get(tok, 0) + 1

        if not token_df:
            return 0.0

        n_patents = len(patents)
        score = 0.0
        for tok, df in token_df.items():
            idf = math.log((n_patents + 1) / (df + 1)) + 1.0
            score += idf

        # Normalize against the maximum possible (all request tokens, max idf)
        max_idf = math.log(n_patents + 1) + 1.0
        max_possible = max_idf * len(request_tokens)
        if max_possible <= 0:
            return 0.0
        return min(score / max_possible * 100, 100.0)

    # ─── Misc helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _extract_patents(professor: Dict[str, Any]) -> List[Dict[str, Any]]:
        patents = professor.get("patents") or []
        if not isinstance(patents, list):
            return []
        # Allow patents to be plain strings (titles only)
        normalized = []
        for p in patents:
            if isinstance(p, dict):
                normalized.append(p)
            elif isinstance(p, str) and p.strip():
                normalized.append({"title": p.strip(), "status": "filed"})
        return normalized

    @staticmethod
    def _extract_co_inventors(patent: Dict[str, Any], exclude_name: str = "") -> List[str]:
        raw = patent.get("inventors") or patent.get("co_inventors") or []
        if isinstance(raw, str):
            raw = [x.strip() for x in re.split(r"[,;]", raw) if x.strip()]
        result = []
        exclude_l = exclude_name.lower().strip()
        for inv in raw:
            name = str(inv).strip()
            if not name:
                continue
            if exclude_l and exclude_l in name.lower():
                continue
            result.append(name)
        return result

    @staticmethod
    def _request_text(request: Any) -> str:
        """Build a single combined text from a CompanyRequest dataclass or dict."""
        if request is None:
            return ""
        getter = lambda k: getattr(request, k, None) if not isinstance(request, dict) else request.get(k)
        parts = []
        for key in ("project_description", "challenges", "industry"):
            val = getter(key)
            if val:
                parts.append(str(val))
        for key in ("technical_area", "required_expertise", "tech_stack"):
            val = getter(key)
            if isinstance(val, list):
                parts.extend(str(v) for v in val)
            elif val:
                parts.append(str(val))
        return " ".join(parts)

    @staticmethod
    def _recent_patent_summaries(patents: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        annotated = []
        for p in patents:
            year = _extract_year(p.get("filing_date") or p.get("year"))
            annotated.append((year or 0, p))
        annotated.sort(key=lambda x: x[0], reverse=True)
        out = []
        for _, p in annotated[:limit]:
            out.append({
                "title": p.get("title", ""),
                "filing_date": p.get("filing_date") or p.get("year") or "",
                "status": _patent_status(p),
                "patent_number": p.get("patent_number") or p.get("application_number") or "",
            })
        return out


__all__ = [
    "PatentScorer",
    "PatentScoringConfig",
    "PatentPortfolioScore",
    "PatentRelevanceScore",
]
