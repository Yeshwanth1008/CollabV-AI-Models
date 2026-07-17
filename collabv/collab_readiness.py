"""
CollabV AI - Model 4: Collaboration Readiness Predictor
=========================================================
Predicts how 'ready' a professor is to take on a new industry collaboration
based on engagement history, publication velocity, patent activity, seniority,
and departmental collaboration culture.

Public API:
    CollabReadinessPredictor().predict_readiness(prof) -> ReadinessScore
    CollabReadinessPredictor().predict_readiness_for_request(prof, req) -> ContextualReadiness
    CollabReadinessPredictor().get_department_readiness(profs) -> dict
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─── Config ─────────────────────────────────────────────────────────────────

@dataclass
class ReadinessConfig:
    industry_weight: float = 0.30
    publication_weight: float = 0.20
    patent_weight: float = 0.15
    seniority_weight: float = 0.20
    infrastructure_weight: float = 0.15

    # Industry engagement time-decay (years)
    industry_recent_max: int = 2
    industry_mid_max: int = 5
    industry_recent_factor: float = 1.0
    industry_mid_factor: float = 0.6
    industry_old_factor: float = 0.3

    # Publication velocity bell curve
    pub_optimal_min: float = 5.0
    pub_optimal_max: float = 15.0
    pub_too_high: float = 20.0
    pub_too_low: float = 2.0

    # Seniority defaults
    seniority_junior: float = 70.0
    seniority_mid: float = 85.0
    seniority_senior: float = 65.0

    # Patent activity recency (years)
    patent_recent_max: int = 2


# Department collaboration culture tiers (score 0-100)
DEPT_INFRASTRUCTURE_TIERS: Dict[str, float] = {
    # Tier 1: strong industry pipeline
    "computer science": 90.0,
    "electrical": 90.0,
    "mechanical": 90.0,
    "chemical": 90.0,
    # Tier 2: active but smaller scale
    "biotechnology": 75.0,
    "engineering design": 75.0,
    "civil": 75.0,
    "metallurgical": 75.0,
    "metallurgy": 75.0,
    "materials": 75.0,
    # Tier 3: research-focused, occasional industry
    "physics": 60.0,
    "chemistry": 60.0,
    "mathematics": 60.0,
    "ocean": 60.0,
    # Tier 4: limited industry collab
    "humanities": 45.0,
    "management": 60.0,
    "aerospace": 70.0,
    "applied mechanics": 65.0,
}


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class ReadinessBreakdown:
    industry_engagement: float
    publication_velocity: float
    patent_activity: float
    seniority_bandwidth: float
    infrastructure: float


@dataclass
class ReadinessScore:
    overall_score: float
    confidence: str          # "high" | "medium" | "low"
    band: str                # "very high" | "high" | "moderate" | "low"
    breakdown: ReadinessBreakdown
    drivers: List[str] = field(default_factory=list)
    blockers: List[str] = field(default_factory=list)
    annual_publication_rate: float = 0.0
    recent_industry_signals: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


@dataclass
class ContextualReadiness:
    base_readiness: ReadinessScore
    contextual_score: float
    contextual_band: str
    matched_industries: List[str] = field(default_factory=list)
    matched_collab_types: List[str] = field(default_factory=list)
    geographic_match: bool = True
    contextual_drivers: List[str] = field(default_factory=list)
    contextual_blockers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_readiness": self.base_readiness.to_dict(),
            "contextual_score": self.contextual_score,
            "contextual_band": self.contextual_band,
            "matched_industries": self.matched_industries,
            "matched_collab_types": self.matched_collab_types,
            "geographic_match": self.geographic_match,
            "contextual_drivers": self.contextual_drivers,
            "contextual_blockers": self.contextual_blockers,
        }


# ─── Helpers ────────────────────────────────────────────────────────────────

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _years_in_text(text: str) -> List[int]:
    if not text:
        return []
    return [int(m.group()) for m in _YEAR_RE.finditer(text)]


def _to_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, list):
        return " ".join(_to_text(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_to_text(v) for v in value.values())
    return str(value)


def _band(score: float) -> str:
    if score >= 80:
        return "very high"
    if score >= 65:
        return "high"
    if score >= 45:
        return "moderate"
    return "low"


def _confidence_for(prof: Dict[str, Any]) -> str:
    """Lower confidence if we have very little data to score from."""
    signal_count = sum([
        bool(prof.get("collaboration_history")),
        bool(prof.get("industry_exposure")),
        bool(prof.get("publications")),
        bool(prof.get("patents")),
        bool(prof.get("experience_years")),
    ])
    if signal_count >= 4:
        return "high"
    if signal_count >= 2:
        return "medium"
    return "low"


# ─── Predictor ──────────────────────────────────────────────────────────────

class CollabReadinessPredictor:
    """Predict a professor's overall and contextual readiness for collaboration."""

    def __init__(self, config: Optional[ReadinessConfig] = None) -> None:
        self.config = config or ReadinessConfig()
        self.current_year = datetime.now().year

    # ─── Public ─────────────────────────────────────────────────────────────

    def predict_readiness(self, professor: Dict[str, Any]) -> ReadinessScore:
        cfg = self.config

        ind_score, ind_signals = self._industry_engagement(professor)
        pub_score, annual_rate = self._publication_velocity(professor)
        pat_score = self._patent_activity(professor)
        sen_score = self._seniority_bandwidth(professor)
        inf_score = self._infrastructure_signal(professor)

        overall = (
            ind_score * cfg.industry_weight
            + pub_score * cfg.publication_weight
            + pat_score * cfg.patent_weight
            + sen_score * cfg.seniority_weight
            + inf_score * cfg.infrastructure_weight
        )
        overall = round(min(max(overall, 0), 100), 1)

        drivers, blockers = self._explain(
            ind_score, pub_score, pat_score, sen_score, inf_score, annual_rate, ind_signals
        )

        return ReadinessScore(
            overall_score=overall,
            confidence=_confidence_for(professor),
            band=_band(overall),
            breakdown=ReadinessBreakdown(
                industry_engagement=round(ind_score, 1),
                publication_velocity=round(pub_score, 1),
                patent_activity=round(pat_score, 1),
                seniority_bandwidth=round(sen_score, 1),
                infrastructure=round(inf_score, 1),
            ),
            drivers=drivers,
            blockers=blockers,
            annual_publication_rate=round(annual_rate, 2),
            recent_industry_signals=ind_signals,
        )

    def predict_readiness_for_request(
        self, professor: Dict[str, Any], request: Any
    ) -> ContextualReadiness:
        base = self.predict_readiness(professor)

        history_text = _to_text(professor.get("collaboration_history")) + " " + _to_text(professor.get("industry_exposure"))
        history_lower = history_text.lower()

        # Industry match
        req_industry = self._safe_get(request, "industry", "")
        req_tech = self._safe_get(request, "technical_area", []) or []
        matched_industries = []
        if req_industry and req_industry.lower() in history_lower:
            matched_industries.append(req_industry)
        for term in req_tech:
            if term and term.lower() in history_lower and term not in matched_industries:
                matched_industries.append(term)

        # Collaboration type match
        req_collab = self._safe_get(request, "collaboration_type", "") or ""
        prof_collab_tags = professor.get("matching_tags", {}).get("collab_type_tags", []) or []
        matched_collab = [c for c in prof_collab_tags if req_collab and (
            req_collab.lower() in c.lower() or c.lower() in req_collab.lower()
        )]

        # Geographic match (very loose)
        loc_pref = (self._safe_get(request, "location_preference", "") or "").lower()
        prof_loc = (professor.get("location") or "").lower()
        if not loc_pref or loc_pref == "any":
            geo_match = True
        else:
            geo_match = any(k in loc_pref for k in ("chennai", "tamil nadu", "south india")) or any(
                k in prof_loc for k in ("chennai", "iitm", "iit madras", "tamil nadu")
            )

        # Apply contextual adjustments
        adj = 0.0
        contextual_drivers = []
        contextual_blockers = []

        if matched_industries:
            adj += min(8, 3 + 2 * len(matched_industries))
            contextual_drivers.append(
                f"Prior experience with: {', '.join(matched_industries[:3])}"
            )
        elif req_industry:
            adj -= 3
            contextual_blockers.append(f"No prior history with {req_industry}")

        if matched_collab:
            adj += 5
            contextual_drivers.append(f"Typical engagement style fits {req_collab}")
        elif req_collab and prof_collab_tags:
            adj -= 2
            contextual_blockers.append(f"Past collaborations differ from {req_collab}")

        if not geo_match:
            adj -= 4
            contextual_blockers.append("Location preference may require remote setup")

        contextual_score = round(max(0, min(100, base.overall_score + adj)), 1)

        return ContextualReadiness(
            base_readiness=base,
            contextual_score=contextual_score,
            contextual_band=_band(contextual_score),
            matched_industries=matched_industries,
            matched_collab_types=matched_collab,
            geographic_match=geo_match,
            contextual_drivers=contextual_drivers,
            contextual_blockers=contextual_blockers,
        )

    def get_department_readiness(self, professors: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_dept: Dict[str, List[float]] = {}
        for p in professors:
            dept = (p.get("department") or "Unknown").replace("Department of ", "")
            score = self.predict_readiness(p).overall_score
            by_dept.setdefault(dept, []).append(score)

        rows = []
        for dept, scores in by_dept.items():
            if not scores:
                continue
            rows.append({
                "department": dept,
                "professor_count": len(scores),
                "avg_readiness": round(sum(scores) / len(scores), 1),
                "max_readiness": round(max(scores), 1),
                "high_readiness_count": sum(1 for s in scores if s >= 70),
            })
        rows.sort(key=lambda r: -r["avg_readiness"])
        return {"departments": rows, "total_departments": len(rows)}

    # ─── Sub-signals ────────────────────────────────────────────────────────

    def _industry_engagement(self, prof: Dict[str, Any]) -> Tuple[float, int]:
        cfg = self.config
        text = _to_text(prof.get("collaboration_history")) + " " + _to_text(prof.get("industry_exposure"))
        if not text.strip():
            return 25.0, 0  # baseline - might just be missing data

        years = _years_in_text(text)
        # Score by most recent year of engagement
        recent_signals = 0
        best_factor = 0.0
        for y in years:
            age = self.current_year - y
            if age <= cfg.industry_recent_max:
                factor = cfg.industry_recent_factor
                recent_signals += 1
            elif age <= cfg.industry_mid_max:
                factor = cfg.industry_mid_factor
            else:
                factor = cfg.industry_old_factor
            best_factor = max(best_factor, factor)

        # Text length proxy for engagement depth
        word_count = len(text.split())
        depth_score = min(word_count / 50, 1.0)  # cap at 50+ words

        if best_factor == 0.0 and not years:
            # Has text but no dates - assume mid-range
            return 40.0 + depth_score * 15, 0

        score = best_factor * 70 + depth_score * 30
        return min(score, 100.0), recent_signals

    def _publication_velocity(self, prof: Dict[str, Any]) -> Tuple[float, float]:
        cfg = self.config
        pubs = prof.get("publications") or []
        if not pubs:
            return 30.0, 0.0

        # Extract years from publication entries
        years: List[int] = []
        for pub in pubs:
            text = _to_text(pub)
            ys = _years_in_text(text)
            if ys:
                years.append(max(ys))

        if not years:
            # We know they have papers but no dates - mid-velocity assumption
            count = len(pubs)
            if count >= 20:
                return 75.0, 5.0
            if count >= 10:
                return 70.0, 3.0
            if count >= 5:
                return 60.0, 1.5
            return 45.0, 0.7

        recent_window_start = self.current_year - 3
        recent_count = sum(1 for y in years if y >= recent_window_start)
        annual_rate = recent_count / 3

        if annual_rate <= 0:
            return 30.0, 0.0
        if annual_rate < cfg.pub_too_low:
            return 40.0 + annual_rate * 15, annual_rate
        if annual_rate <= cfg.pub_optimal_max:
            # Bell-ish peak in optimal range
            if cfg.pub_optimal_min <= annual_rate <= cfg.pub_optimal_max:
                return 90.0, annual_rate
            return 70.0 + (annual_rate - cfg.pub_too_low) * 5, annual_rate
        if annual_rate < cfg.pub_too_high:
            return max(60.0, 90.0 - (annual_rate - cfg.pub_optimal_max) * 4), annual_rate
        return 55.0, annual_rate  # very high - might be too busy

    def _patent_activity(self, prof: Dict[str, Any]) -> float:
        cfg = self.config
        patents = prof.get("patents") or []
        if not patents:
            return 25.0

        # Lazy import to avoid hard dependency cycle
        try:
            from .patent_scorer import PatentScorer
            insights = PatentScorer().get_patent_insights(prof)
            base = insights["portfolio"]["total_score"] * 0.7
            recent_year = insights["portfolio"]["newest_patent_year"] or 0
            if self.current_year - recent_year <= cfg.patent_recent_max:
                base += 20
            return min(base, 100.0)
        except Exception:
            logger.debug("Falling back to simple patent activity score")
            n = len(patents) if isinstance(patents, list) else 1
            return min(40 + n * 5, 95)

    def _seniority_bandwidth(self, prof: Dict[str, Any]) -> float:
        cfg = self.config
        level = (prof.get("seniority_level") or "").lower()
        years_raw = prof.get("experience_years")
        if isinstance(years_raw, (int, float)):
            years = int(years_raw)
        else:
            nums = re.findall(r"\d+", str(years_raw or ""))
            years = (int(nums[0]) + int(nums[1])) // 2 if len(nums) >= 2 else (int(nums[0]) if nums else 0)

        if "junior" in level:
            return cfg.seniority_junior
        if "senior" in level or "professor" in level and years >= 20:
            return cfg.seniority_senior
        if "mid" in level or "associate" in level:
            return cfg.seniority_mid

        # Fallback by experience years
        if years <= 0:
            return 70.0
        if years < 10:
            return cfg.seniority_junior
        if years < 20:
            return cfg.seniority_mid
        return cfg.seniority_senior

    def _infrastructure_signal(self, prof: Dict[str, Any]) -> float:
        dept = (prof.get("department") or "").lower().replace("department of ", "")
        # Find best-matching dept tier
        best = 0.0
        for key, score in DEPT_INFRASTRUCTURE_TIERS.items():
            if key in dept:
                best = max(best, score)
        if best == 0.0:
            best = 55.0  # neutral baseline for unknown dept

        # Boost if professor has explicit collaboration history (infrastructure proxy)
        if prof.get("collaboration_history"):
            best = min(best + 5, 100)
        return best

    # ─── Explanation ────────────────────────────────────────────────────────

    @staticmethod
    def _explain(
        ind: float, pub: float, pat: float, sen: float, inf: float,
        annual_rate: float, recent_signals: int,
    ) -> Tuple[List[str], List[str]]:
        drivers, blockers = [], []
        if ind >= 70:
            label = "Active industry engagement" + (
                f" (last activity <{2} yrs)" if recent_signals else ""
            )
            drivers.append(label)
        elif ind < 40:
            blockers.append("Limited or stale industry history")
        if pub >= 75:
            drivers.append(f"Healthy publication velocity (~{annual_rate:.1f}/yr)")
        elif pub < 45:
            blockers.append("Low recent publication velocity")
        if pat >= 70:
            drivers.append("Strong patent activity / innovation orientation")
        if sen >= 80:
            drivers.append("Mid-career seniority - good bandwidth")
        elif sen < 65:
            blockers.append("Seniority may limit bandwidth (very junior or very senior)")
        if inf >= 80:
            drivers.append("Department has strong industry pipeline")
        elif inf < 55:
            blockers.append("Department has limited industry infrastructure")
        return drivers, blockers

    # ─── Misc ───────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_get(request: Any, key: str, default: Any) -> Any:
        if request is None:
            return default
        if isinstance(request, dict):
            return request.get(key, default)
        return getattr(request, key, default)


__all__ = [
    "CollabReadinessPredictor",
    "ReadinessConfig",
    "ReadinessScore",
    "ContextualReadiness",
    "ReadinessBreakdown",
    "DEPT_INFRASTRUCTURE_TIERS",
]
