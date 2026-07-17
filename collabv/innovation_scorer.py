"""
CollabV AI - Innovation Scoring (the 6th factor from the spec)
================================================================
Detects cross-domain novelty opportunities — situations where a professor's
expertise spans multiple domains that the company request touches, or where
the professor sits at the intersection of two normally-disconnected fields
that the request needs bridged.

Higher score = more likely to produce novel inventions rather than incremental
work in a single field.

Heuristics (each contributes 0-100):
  1. Domain breadth      — how many distinct domains the professor operates in
  2. Cross-domain bridge — does the professor span the *specific* domains the
                            request needs combined? (e.g. "ML for materials"
                            scores higher for a Materials Eng professor who
                            publishes ML papers than for a pure ML professor
                            or a pure Materials professor)
  3. Patent diversity    — multi-domain patent portfolio = innovation signal
  4. Publication entropy — varied publication topics suggest cross-pollination
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


# ─── Domain taxonomy (reuses categories from patent_scorer where possible) ─

_DOMAIN_KEYWORDS: Dict[str, List[str]] = {
    "ai_ml":          ["machine learning", "deep learning", "neural", "nlp",
                       "computer vision", "reinforcement", "transformer", "ai "],
    "robotics":       ["robotic", "manipulator", "autonomous", "drone", "uav",
                       "gripper", "humanoid"],
    "energy":         ["solar", "battery", "fuel cell", "renewable", "hydrogen",
                       "photovoltaic", "energy storage", "grid"],
    "materials":      ["materials", "alloy", "composite", "polymer", "ceramic",
                       "metallurgy", "nanomaterial"],
    "biotech":        ["biotechnology", "enzyme", "protein", "genom", "drug",
                       "vaccine", "biopolymer", "biomarker"],
    "chemicals":      ["catalyst", "polymerization", "separation", "reaction",
                       "petrochemical", "membrane"],
    "civil":          ["structural", "concrete", "geotechnical", "transportation",
                       "seismic", "construction"],
    "aerospace":      ["aerospace", "aircraft", "propulsion", "spacecraft",
                       "aerodynamic", "satellite"],
    "electronics":    ["vlsi", "antenna", "wireless", "5g", "rf ",
                       "power electronics", "semiconductor"],
    "fluid_thermal":  ["fluid", "thermal", "heat transfer", "combustion", "cfd",
                       "turbomachinery"],
    "sensors_iot":    ["sensor", "iot", "wearable", "biomedical device"],
    "optics":         ["optical", "photonic", "laser", "fiber optic"],
    "healthcare":     ["medical", "diagnostic", "prosthet", "implant", "surgical",
                       "rehabilitation"],
    "manufacturing":  ["manufacturing", "machining", "additive manufacturing",
                       "3d printing", "metrology"],
    "data_science":   ["data science", "statistics", "optimization", "analytics",
                       "time series"],
}


# Pairs of domains that are typically siloed; bridging them is high-value.
# Higher number = more rare = bigger innovation bonus when bridged.
_CROSS_DOMAIN_BONUSES: Dict[tuple, float] = {
    frozenset({"ai_ml", "materials"}):       1.5,
    frozenset({"ai_ml", "civil"}):           1.4,
    frozenset({"ai_ml", "aerospace"}):       1.3,
    frozenset({"ai_ml", "chemicals"}):       1.4,
    frozenset({"ai_ml", "biotech"}):         1.2,
    frozenset({"ai_ml", "healthcare"}):      1.2,
    frozenset({"robotics", "biotech"}):      1.5,
    frozenset({"robotics", "healthcare"}):   1.3,
    frozenset({"energy", "materials"}):      1.2,
    frozenset({"energy", "ai_ml"}):          1.3,
    frozenset({"sensors_iot", "civil"}):     1.2,
    frozenset({"sensors_iot", "healthcare"}): 1.2,
    frozenset({"optics", "ai_ml"}):          1.4,
    frozenset({"biotech", "materials"}):     1.3,
    frozenset({"manufacturing", "ai_ml"}):   1.2,
}


# ─── Config + result types ────────────────────────────────────────────────

@dataclass
class InnovationConfig:
    breadth_weight: float = 0.25
    bridge_weight: float = 0.45
    patent_diversity_weight: float = 0.15
    publication_entropy_weight: float = 0.15

    breadth_optimal_min: int = 3   # Hitting 3+ domains = max breadth signal
    breadth_optimal_max: int = 6   # Above 6, treat as scattered (slight penalty)


@dataclass
class InnovationScore:
    total_score: float
    breadth_score: float
    bridge_score: float
    patent_diversity_score: float
    publication_entropy_score: float
    professor_domains: List[str] = field(default_factory=list)
    request_domains: List[str] = field(default_factory=list)
    bridges_detected: List[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _classify_text(text: str) -> List[str]:
    """Return list of domain keys found in text."""
    text_lower = text.lower()
    hits = []
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            hits.append(domain)
    return hits


def _professor_text(prof: Dict[str, Any]) -> str:
    parts = [prof.get("biography", "") or ""]
    parts.extend(prof.get("research_areas", []) or [])
    parts.extend(prof.get("technical_expertise", []) or [])
    parts.extend(str(p) for p in (prof.get("publications") or [])[:10])
    for pat in (prof.get("patents") or [])[:15]:
        if isinstance(pat, dict):
            parts.append(pat.get("title", ""))
        else:
            parts.append(str(pat))
    return " ".join(p for p in parts if p)


def _request_text(request: Any) -> str:
    if request is None:
        return ""
    getter = (lambda k: request.get(k) if isinstance(request, dict)
              else getattr(request, k, None))
    parts = []
    for key in ("project_description", "challenges", "industry"):
        v = getter(key)
        if v:
            parts.append(str(v))
    for key in ("technical_area", "required_expertise", "tech_stack"):
        v = getter(key)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return " ".join(parts)


def _patent_domains(prof: Dict[str, Any]) -> List[List[str]]:
    """Per-patent domain classification (one inner list per patent)."""
    out = []
    for pat in (prof.get("patents") or []):
        if isinstance(pat, dict):
            text = f"{pat.get('title','')} {pat.get('abstract','')}"
        else:
            text = str(pat)
        out.append(_classify_text(text))
    return out


def _publication_entropy(prof: Dict[str, Any]) -> float:
    """Shannon entropy of domain distribution over the professor's publications.

    Returns a 0..1 normalized value (max entropy / log2(n_domains)).
    """
    pubs = prof.get("publications") or []
    if not pubs:
        return 0.0
    domain_counts: Counter = Counter()
    for pub in pubs[:30]:
        for d in _classify_text(str(pub)):
            domain_counts[d] += 1
    if not domain_counts:
        return 0.0
    total = sum(domain_counts.values())
    probs = [c / total for c in domain_counts.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(len(_DOMAIN_KEYWORDS))
    if max_entropy <= 0:
        return 0.0
    return min(entropy / max_entropy, 1.0)


# ─── Public scorer ────────────────────────────────────────────────────────

class InnovationScorer:
    """Score how likely a professor is to drive novel/cross-domain work."""

    def __init__(self, config: Optional[InnovationConfig] = None) -> None:
        self.config = config or InnovationConfig()

    def score(self, professor: Dict[str, Any], request: Any) -> InnovationScore:
        prof_text = _professor_text(professor)
        req_text = _request_text(request)

        prof_domains = set(_classify_text(prof_text))
        req_domains = set(_classify_text(req_text))

        breadth = self._breadth_score(prof_domains)
        bridge, bridges_detected = self._bridge_score(prof_domains, req_domains)
        patent_div = self._patent_diversity(professor)
        pub_entropy = _publication_entropy(professor) * 100

        cfg = self.config
        total = (
            breadth * cfg.breadth_weight
            + bridge * cfg.bridge_weight
            + patent_div * cfg.patent_diversity_weight
            + pub_entropy * cfg.publication_entropy_weight
        )

        reason = self._narrate(prof_domains, req_domains, bridges_detected, total)

        return InnovationScore(
            total_score=round(min(total, 100), 1),
            breadth_score=round(breadth, 1),
            bridge_score=round(bridge, 1),
            patent_diversity_score=round(patent_div, 1),
            publication_entropy_score=round(pub_entropy, 1),
            professor_domains=sorted(prof_domains),
            request_domains=sorted(req_domains),
            bridges_detected=bridges_detected,
            reason=reason,
        )

    # ─── Sub-scores ──────────────────────────────────────────────────────

    def _breadth_score(self, prof_domains: set) -> float:
        cfg = self.config
        n = len(prof_domains)
        if n == 0:
            return 20.0  # No signal - neutral-low
        if n == 1:
            return 30.0  # Pure specialist - low innovation prior
        if n == 2:
            return 55.0
        if cfg.breadth_optimal_min <= n <= cfg.breadth_optimal_max:
            return 85.0  # Sweet spot
        # >6 domains: treat as too scattered, mild penalty
        return 70.0

    def _bridge_score(self, prof_domains: set, req_domains: set) -> tuple[float, list]:
        """Did the professor bridge the *specific* domains the request needs?"""
        if not prof_domains or not req_domains:
            return 30.0, []

        # Direct domain overlap: professor covers ALL the request's domains
        overlap = prof_domains & req_domains
        if not overlap:
            return 15.0, []  # Professor doesn't cover ANY of the request's domains - poor bridge candidate

        coverage = len(overlap) / len(req_domains)
        base = coverage * 60  # Up to 60 from coverage alone

        # Bonus for sitting at a known high-value intersection
        bridges_named = []
        bonus = 0.0
        for pair, multiplier in _CROSS_DOMAIN_BONUSES.items():
            if pair.issubset(prof_domains) and (pair & req_domains):
                # Professor bridges a recognized hard intersection that
                # touches the request's domains.
                a, b = list(pair)
                bridges_named.append(f"{a} × {b}")
                bonus += 15 * multiplier  # ~22 pts per bridge

        return min(base + bonus, 100.0), bridges_named

    def _patent_diversity(self, professor: Dict[str, Any]) -> float:
        per_patent = _patent_domains(professor)
        if not per_patent:
            return 25.0
        all_domains: set = set()
        for ds in per_patent:
            all_domains.update(ds)
        if not all_domains:
            return 25.0
        # Map # distinct patent-domains to a 0-100 score
        n = len(all_domains)
        if n == 1:
            return 35.0
        if n == 2:
            return 60.0
        if n == 3:
            return 80.0
        return min(85 + (n - 3) * 4, 100)

    @staticmethod
    def _narrate(prof_domains: set, req_domains: set,
                 bridges: list, total: float) -> str:
        if total >= 75 and bridges:
            return ("Strong cross-domain innovator at " + ", ".join(bridges[:2])
                    + " — high novelty potential")
        if total >= 75:
            return ("Operates across " + str(len(prof_domains)) +
                    " domains — broad innovation surface")
        if total >= 55 and (prof_domains & req_domains):
            return ("Covers " + str(len(prof_domains & req_domains)) +
                    "/" + str(max(len(req_domains), 1)) +
                    " of the request's domains")
        if not (prof_domains & req_domains):
            return "Limited domain alignment with this request"
        return "Moderate innovation signal"


__all__ = ["InnovationScorer", "InnovationConfig", "InnovationScore"]
