"""
CollabV AI - Model 6: Deal Success Probability Scoring
========================================================
Predicts the likelihood that a specific professor-company match will result in
a successful collaboration, given match quality, alignment breadth, historical
patterns, complexity-expertise fit, and practical feasibility.

Public API:
    DealScorer().score_deal(match_result, professor, company_request) -> DealAssessment
    DealScorer().batch_score(match_results, professors_by_id, request) -> List[DealAssessment]
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ─── Config ─────────────────────────────────────────────────────────────────

@dataclass
class DealScoringConfig:
    match_quality_weight: float = 0.35
    breadth_weight: float = 0.15
    history_weight: float = 0.20
    complexity_weight: float = 0.15
    feasibility_weight: float = 0.15

    breadth_threshold: float = 70.0
    high_confidence_feedback: int = 10
    medium_confidence_feedback: int = 3

    # Timeline feasibility (months)
    min_realistic_timeline: int = 3
    typical_min_timeline: int = 6
    typical_max_timeline: int = 18


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class RiskFactor:
    category: str
    description: str
    severity: str            # "high" | "medium" | "low"
    mitigation: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DealAssessment:
    professor_id: str
    professor_name: str
    success_probability: float       # 0.0 - 1.0
    success_percent: float           # 0 - 100
    confidence_level: str            # "high" | "medium" | "low"
    band: str                        # "strong" | "moderate" | "exploratory" | "risky"
    risk_factors: List[RiskFactor] = field(default_factory=list)
    opportunity_factors: List[str] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    estimated_timeline_fit: bool = True
    factor_breakdown: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["risk_factors"] = [r.to_dict() if isinstance(r, RiskFactor) else r for r in self.risk_factors]
        return d


# ─── Helpers ────────────────────────────────────────────────────────────────

def _parse_years(value: Any) -> int:
    """Robustly parse experience_years which can be int, '8 years', '8-15 years', etc."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    import re as _re
    nums = _re.findall(r"\d+", str(value))
    if not nums:
        return 0
    if len(nums) == 1:
        return int(nums[0])
    # range like "8-15": use midpoint
    return (int(nums[0]) + int(nums[1])) // 2


def _sigmoid(x: float) -> float:
    if x >= 35:
        return 1.0
    if x <= -35:
        return 0.0
    return 1.0 / (1.0 + math.exp(-x))


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _band(prob: float) -> str:
    if prob >= 0.70:
        return "strong"
    if prob >= 0.50:
        return "moderate"
    if prob >= 0.30:
        return "exploratory"
    return "risky"


# ─── Scorer ─────────────────────────────────────────────────────────────────

class DealScorer:
    """Predict the success probability of a specific match."""

    def __init__(
        self,
        config: Optional[DealScoringConfig] = None,
        feedback_provider: Optional[Any] = None,
    ) -> None:
        """
        Args:
            config: Scoring config overrides.
            feedback_provider: Optional callable or object exposing
                `get_professor_feedback(professor_id)` -> dict with keys
                `accept_count`, `reject_count`, `total`. If not provided, the
                scorer falls back to neutral history scoring.
        """
        self.config = config or DealScoringConfig()
        self.feedback_provider = feedback_provider

    # ─── Public ─────────────────────────────────────────────────────────────

    def score_deal(
        self,
        match_result: Dict[str, Any],
        professor: Dict[str, Any],
        company_request: Any,
    ) -> DealAssessment:
        cfg = self.config

        # 5 signals, each on a 0-100 scale, then combined via sigmoid for probability
        match_quality, mq_reasons = self._match_quality(match_result)
        breadth, breadth_reason = self._alignment_breadth(match_result)
        history, history_conf, history_reason = self._historical_pattern(
            professor, company_request
        )
        complexity, complexity_reason = self._complexity_expertise_match(
            professor, company_request
        )
        feasibility, feasibility_reasons, timeline_fit = self._practical_feasibility(
            company_request, match_result
        )

        weighted = (
            match_quality * cfg.match_quality_weight
            + breadth * cfg.breadth_weight
            + history * cfg.history_weight
            + complexity * cfg.complexity_weight
            + feasibility * cfg.feasibility_weight
        )
        # Convert 0-100 weighted score to logistic probability.
        # Center at 50, sharpness chosen so 75->~0.78, 50->0.5, 25->~0.22.
        prob = _sigmoid((weighted - 50.0) / 12.0)

        risks, opportunities, actions = self._narrative(
            match_quality, breadth, history, complexity, feasibility,
            mq_reasons, breadth_reason, history_reason,
            complexity_reason, feasibility_reasons, timeline_fit,
        )

        confidence = self._overall_confidence(history_conf, professor)

        return DealAssessment(
            professor_id=str(professor.get("professor_id", "")),
            professor_name=str(professor.get("name", match_result.get("professor_name", ""))),
            success_probability=round(prob, 3),
            success_percent=round(prob * 100, 1),
            confidence_level=confidence,
            band=_band(prob),
            risk_factors=risks,
            opportunity_factors=opportunities,
            recommended_actions=actions,
            estimated_timeline_fit=timeline_fit,
            factor_breakdown={
                "match_quality": round(match_quality, 1),
                "alignment_breadth": round(breadth, 1),
                "historical_pattern": round(history, 1),
                "complexity_expertise": round(complexity, 1),
                "practical_feasibility": round(feasibility, 1),
            },
        )

    def batch_score(
        self,
        match_results: Sequence[Dict[str, Any]],
        professors_by_id: Dict[str, Dict[str, Any]],
        request: Any,
    ) -> List[DealAssessment]:
        out: List[DealAssessment] = []
        for m in match_results:
            pid = str(m.get("professor_id") or "")
            prof = professors_by_id.get(pid)
            if not prof:
                # Fall back to a stub - we still want a deal assessment for every match
                prof = {
                    "professor_id": pid,
                    "name": m.get("professor_name", ""),
                    "department": m.get("department", ""),
                }
            out.append(self.score_deal(m, prof, request))
        return out

    # ─── Signal computations ────────────────────────────────────────────────

    def _match_quality(self, match_result: Dict[str, Any]) -> tuple[float, List[str]]:
        score = float(match_result.get("score", 0) or 0)
        tiers = [
            float(match_result.get("tier1_score", 0) or 0),
            float(match_result.get("tier2_score", 0) or 0),
            float(match_result.get("tier3_score", 0) or 0),
        ]

        reasons = []
        # Penalize if the entire match is carried by one tier
        nonzero = [t for t in tiers if t > 5]
        if score >= 80:
            reasons.append("Very strong composite match")
            base = score
        elif score >= 60:
            base = score
        else:
            reasons.append(f"Composite score {score:.0f} indicates weak alignment")
            base = score * 0.9

        if len(nonzero) <= 1:
            reasons.append("Match driven by a single dimension - narrow fit")
            base = base * 0.85

        return min(max(base, 0), 100), reasons

    def _alignment_breadth(self, match_result: Dict[str, Any]) -> tuple[float, str]:
        cfg = self.config
        factors = [
            float(match_result.get("tier1_score", 0) or 0),
            float(match_result.get("tier2_score", 0) or 0),
            float(match_result.get("tier3_score", 0) or 0),
            float(match_result.get("patent_score", 0) or 0),
            float(match_result.get("readiness_score", 0) or 0),
            float(match_result.get("contextual_readiness", 0) or 0),
        ]
        # Only count those that are nonzero (not all signals are always present)
        present = [f for f in factors if f > 0]
        if not present:
            return 30.0, "No factor scores available"
        strong = sum(1 for f in present if f >= cfg.breadth_threshold)
        if strong >= 5:
            return 95.0, f"{strong} factors above {cfg.breadth_threshold:.0f} - broad alignment"
        if strong >= 3:
            return 80.0, f"{strong} factors above {cfg.breadth_threshold:.0f} - good breadth"
        if strong >= 2:
            return 60.0, f"{strong} factors above {cfg.breadth_threshold:.0f} - moderate breadth"
        if strong == 1:
            return 40.0, "Only 1 factor above threshold - over-indexed"
        return 25.0, "No factors strongly above threshold"

    def _historical_pattern(
        self, professor: Dict[str, Any], request: Any
    ) -> tuple[float, str, str]:
        pid = professor.get("professor_id")
        if not self.feedback_provider or not pid:
            return 50.0, "low", "No feedback history available"
        try:
            stats = self.feedback_provider.get_professor_feedback(pid)
        except Exception as e:
            logger.debug("feedback_provider failed: %s", e)
            return 50.0, "low", "Feedback provider error"

        total = int(stats.get("total", 0))
        if total <= 0:
            return 50.0, "low", "No history yet"

        accept = int(stats.get("accept_count", 0))
        reject = int(stats.get("reject_count", 0))

        accept_rate = accept / total
        if total >= self.config.high_confidence_feedback:
            conf = "high"
        elif total >= self.config.medium_confidence_feedback:
            conf = "medium"
        else:
            conf = "low"

        # Map accept_rate to a 0-100 score
        if accept_rate >= 0.6:
            score = 80.0 + (accept_rate - 0.6) * 50
        elif accept_rate >= 0.4:
            score = 60.0 + (accept_rate - 0.4) * 100
        elif accept_rate >= 0.2:
            score = 40.0 + (accept_rate - 0.2) * 100
        else:
            score = max(20.0 + accept_rate * 100, 15.0)

        reason = (
            f"{accept}/{total} accepted ({accept_rate*100:.0f}%) - history "
            + ("favorable" if accept_rate >= 0.5 else "unfavorable")
        )
        return min(score, 100.0), conf, reason

    def _complexity_expertise_match(
        self, professor: Dict[str, Any], request: Any
    ) -> tuple[float, str]:
        level = (_safe_get(request, "research_level", "") or "").lower()
        seniority = (professor.get("seniority_level") or "").lower()
        years = _parse_years(professor.get("experience_years"))

        # Normalize seniority
        if "senior" in seniority or years >= 20:
            prof_band = "senior"
        elif "junior" in seniority or years < 10:
            prof_band = "junior"
        else:
            prof_band = "mid"

        # Pair compatibility
        if "fundamental" in level or "deep" in level:
            if prof_band == "senior":
                return 90.0, "Senior expertise matched to deep research need"
            if prof_band == "mid":
                return 70.0, "Mid-career fit for deep research"
            return 45.0, "Deep research need - junior fit may struggle"
        if "applied" in level or "product" in level:
            if prof_band == "mid":
                return 90.0, "Mid-career professor ideal for applied work"
            if prof_band == "junior":
                return 75.0, "Junior fit for applied work"
            return 70.0, "Senior may have bandwidth constraints for applied work"
        if "basic" in level:
            if prof_band == "junior":
                return 85.0, "Junior fit for basic R&D"
            return 70.0, "Reasonable fit"
        return 65.0, "Research level not specified - neutral fit"

    def _practical_feasibility(
        self, request: Any, match_result: Dict[str, Any]
    ) -> tuple[float, List[str], bool]:
        cfg = self.config
        reasons: List[str] = []
        timeline_fit = True

        timeline = int(_safe_get(request, "timeline_months", 0) or 0)
        if timeline > 0:
            if timeline < cfg.min_realistic_timeline:
                timeline_fit = False
                reasons.append(
                    f"Timeline {timeline} months below realistic minimum"
                )
                timeline_score = 25.0
            elif timeline < cfg.typical_min_timeline:
                reasons.append(f"Timeline {timeline} months is aggressive")
                timeline_score = 55.0
            elif timeline <= cfg.typical_max_timeline:
                timeline_score = 90.0
            else:
                reasons.append("Timeline is long - may dilute urgency")
                timeline_score = 75.0
        else:
            timeline_score = 65.0  # neutral

        budget = (_safe_get(request, "budget_tier", "") or "medium").lower()
        budget_score = 70.0
        if "low" in budget:
            budget_score = 55.0
            reasons.append("Low budget tier - scope may need to shrink")
        elif "high" in budget:
            budget_score = 85.0

        # Location score
        loc = (_safe_get(request, "location_preference", "") or "any").lower()
        if not loc or loc == "any":
            loc_score = 85.0
        elif any(k in loc for k in ("chennai", "tamil", "south india")):
            loc_score = 90.0
        else:
            loc_score = 70.0
            reasons.append("Location preference outside Chennai - may need travel")

        score = timeline_score * 0.5 + budget_score * 0.3 + loc_score * 0.2
        return score, reasons, timeline_fit

    # ─── Narrative generation ──────────────────────────────────────────────

    def _narrative(
        self,
        mq: float, breadth: float, history: float, complexity: float, feasibility: float,
        mq_reasons: List[str], breadth_reason: str, history_reason: str,
        complexity_reason: str, feasibility_reasons: List[str], timeline_fit: bool,
    ) -> tuple[List[RiskFactor], List[str], List[str]]:
        risks: List[RiskFactor] = []
        opportunities: List[str] = []
        actions: List[str] = []

        if mq < 55:
            risks.append(RiskFactor(
                category="expertise_gap",
                description=mq_reasons[0] if mq_reasons else "Composite match score is weak",
                severity="high" if mq < 40 else "medium",
                mitigation="Reframe the project brief or expand search criteria",
            ))
        else:
            opportunities.append("Strong composite match score")

        if breadth < 50:
            risks.append(RiskFactor(
                category="narrow_fit",
                description=breadth_reason,
                severity="medium",
                mitigation="Validate the single strong dimension with a scoping call",
            ))
        elif breadth >= 80:
            opportunities.append(breadth_reason)

        if history < 40:
            risks.append(RiskFactor(
                category="historical_pattern",
                description=history_reason,
                severity="medium",
                mitigation="Initiate with a short consulting engagement before committing",
            ))
        elif history >= 75:
            opportunities.append("Professor has a strong acceptance track record")

        if complexity < 55:
            risks.append(RiskFactor(
                category="complexity_match",
                description=complexity_reason,
                severity="medium",
                mitigation="Pair with a senior advisor or adjust scope",
            ))
        elif complexity >= 85:
            opportunities.append(complexity_reason)

        if feasibility < 55 or not timeline_fit:
            risks.append(RiskFactor(
                category="feasibility",
                description="; ".join(feasibility_reasons) or "Practical constraints flagged",
                severity="high" if not timeline_fit else "medium",
                mitigation="Renegotiate timeline or budget before kicking off",
            ))

        # Recommended actions
        if mq >= 70 and feasibility >= 60:
            actions.append("Send an introduction email; propose a 30-min scoping call")
        if history < 50:
            actions.append("Share a one-pager that highlights expected scope and IP terms")
        if not timeline_fit:
            actions.append("Adjust expected timeline to at least 6 months")
        if complexity < 55:
            actions.append("Validate technical depth in initial call")
        if not actions:
            actions.append("Proceed with standard outreach process")

        return risks, opportunities, actions

    # ─── Confidence ────────────────────────────────────────────────────────

    @staticmethod
    def _overall_confidence(history_conf: str, professor: Dict[str, Any]) -> str:
        signal_count = sum([
            bool(professor.get("publications")),
            bool(professor.get("patents")),
            bool(professor.get("collaboration_history")),
        ])
        if history_conf == "high" and signal_count >= 2:
            return "high"
        if history_conf in ("high", "medium") and signal_count >= 2:
            return "medium"
        return "low"


# ─── Default feedback provider (SQLite-backed) ──────────────────────────────

class SQLiteFeedbackProvider:
    """Pulls per-professor accept/reject counts from the collabv feedback table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def get_professor_feedback(self, professor_id: str) -> Dict[str, Any]:
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute(
                """SELECT action, COUNT(*) FROM feedback
                   WHERE professor_id = ?
                   GROUP BY action""",
                (professor_id,),
            )
            counts: Dict[str, int] = {}
            for action, n in cur.fetchall():
                counts[(action or "").lower()] = n
        finally:
            conn.close()
        accept = counts.get("accept", 0) + counts.get("accepted", 0)
        reject = counts.get("reject", 0) + counts.get("rejected", 0) + counts.get("not_interested", 0)
        total = accept + reject
        return {"accept_count": accept, "reject_count": reject, "total": total}


__all__ = [
    "DealScorer",
    "DealScoringConfig",
    "DealAssessment",
    "RiskFactor",
    "SQLiteFeedbackProvider",
]
