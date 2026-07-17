"""
CollabV AI - Marketplace reranker.

Cold-start path (Phase 1): a deterministic weighted blend of content features.
NO learned model, no training data — every score is reproducible from the
feature inputs alone. This is intentional: per the user's decision #8,
prof-company match feedback is NOT used as labels for the marketplace
reranker, and we wait for real marketplace events before training LTR.

Phase 3 will add a LightGBM LambdaMART model that takes over once we have
~500+ events grouped by query_hash. The interface here is designed so the
warm-path swap is local to this file.

Feature set (all 0-1 normalized before weighting, then composite is rescaled
to 0-100 for display consistency with MatchResult):

  retrieval         - cosine similarity from MarketplaceIndex
  domain_overlap    - shared domain tags / max(buyer_domains, listing_domains)
  industry_match    - exact / partial overlap on industry + industries_of_interest
  recency           - listing recency decay (1y -> 1.0, 5y -> ~0.5, >10y -> 0.2)
  maturity_match    - buyer.tech_maturity_preference vs patent recency proxy
  budget_compat     - buyer.budget_band vs listing.asking_price_inr
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


# ─── Config (defaults are production-safe) ───────────────────────────────

@dataclass
class ColdStartWeights:
    """Weighted blend - must sum to 1.0 (normalized at runtime if not)."""
    retrieval: float       = 0.45   # primary signal at cold start
    domain_overlap: float  = 0.20
    industry_match: float  = 0.15
    recency: float         = 0.10
    maturity_match: float  = 0.05
    budget_compat: float   = 0.05

    def as_dict(self) -> Dict[str, float]:
        return {
            "retrieval": self.retrieval,
            "domain_overlap": self.domain_overlap,
            "industry_match": self.industry_match,
            "recency": self.recency,
            "maturity_match": self.maturity_match,
            "budget_compat": self.budget_compat,
        }


@dataclass
class FeatureScores:
    """Per-candidate feature values (0-1 each)."""
    retrieval: float = 0.0
    domain_overlap: float = 0.0
    industry_match: float = 0.0
    recency: float = 0.0
    maturity_match: float = 0.0
    budget_compat: float = 0.0

    def as_dict(self) -> Dict[str, float]:
        return {
            "retrieval": self.retrieval,
            "domain_overlap": self.domain_overlap,
            "industry_match": self.industry_match,
            "recency": self.recency,
            "maturity_match": self.maturity_match,
            "budget_compat": self.budget_compat,
        }


# ─── Feature extractors ──────────────────────────────────────────────────

def _norm_tokens(items: Any) -> set:
    if not items:
        return set()
    if isinstance(items, str):
        items = [items]
    return {str(x).strip().lower() for x in items if str(x).strip()}


def _jaccard_recall(a: set, b: set) -> float:
    """Recall-biased overlap: |a∩b| / |a|. Returns 0 when either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / max(len(a), 1)


_MATURITY_TO_AGE_YEARS = {
    "early_stage": 2,    # buyer wants fresh tech -> patent <=2y is ideal
    "mid_stage":   6,    # 2-6y window
    "proven":      999,  # no upper bound; older is fine
}

_BUDGET_BAND_INR = {
    "low":         (0,        50_00_000),     # up to ₹50L
    "medium":      (10_00_000, 5_00_00_000),  # ₹10L - ₹5Cr
    "high":        (5_00_00_000, 50_00_00_000),  # ₹5Cr - ₹50Cr
    "enterprise":  (5_00_00_000, float("inf")),
}


def _parse_year(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1900 <= value <= 2100 else None
    s = str(value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + 4], fmt).year
        except ValueError:
            continue
    # Fallback: find 4 digits
    import re
    m = re.search(r"(19|20)\d{2}", s)
    return int(m.group()) if m else None


def _recency_score(listing: Dict[str, Any]) -> float:
    """Exponential decay on granted_date. 0y -> 1.0, 5y -> 0.5, 15y -> ~0.1."""
    year = _parse_year(listing.get("granted_date"))
    if year is None:
        return 0.4  # neutral when unknown
    current_year = datetime.now().year
    age = max(current_year - year, 0)
    return math.exp(-0.14 * age)


def _maturity_match(listing: Dict[str, Any], buyer: Dict[str, Any]) -> float:
    """How well the patent's age fits the buyer's maturity preference."""
    pref = (buyer.get("tech_maturity_preference") or "").lower()
    cap = _MATURITY_TO_AGE_YEARS.get(pref)
    if cap is None:
        return 0.5
    year = _parse_year(listing.get("granted_date"))
    if year is None:
        return 0.5
    age = max(datetime.now().year - year, 0)
    if age <= cap:
        # Linear: best when fresh, decays linearly to 0.5 at cap
        return 1.0 - 0.5 * (age / max(cap, 1))
    return max(0.5 - 0.1 * (age - cap), 0.0)


def _budget_compat(listing: Dict[str, Any], buyer: Dict[str, Any]) -> float:
    """Is the asking price within the buyer's budget band?"""
    price = listing.get("asking_price_inr")
    if price is None or price <= 0:
        return 0.7  # "open to negotiation" - neutral-positive
    band = (buyer.get("budget_band") or "medium").lower()
    lo, hi = _BUDGET_BAND_INR.get(band, _BUDGET_BAND_INR["medium"])
    if lo <= price <= hi:
        return 1.0
    if price < lo:
        return 0.7   # under budget - fine
    # Above ceiling — penalize proportionally
    over = (price - hi) / max(hi, 1)
    return max(0.4 - over * 0.3, 0.0)


def _classify_via_innovation_scorer(text: str) -> set:
    """Run the canonical _DOMAIN_KEYWORDS from collabv.innovation_scorer over
    free-form text. Single source of truth - patent_scorer, innovation_scorer,
    and the marketplace reranker all use the same taxonomy.

    Lazy-imported so the reranker still loads even if innovation_scorer can't
    (it's an engine-side dependency, not strictly required at import time).
    """
    try:
        from .innovation_scorer import _classify_text
        return set(_classify_text(text or ""))
    except Exception:
        return set()


def _listing_topical_text(listing: Dict[str, Any]) -> str:
    """All listing text that should feed topical/domain classification."""
    parts = [
        listing.get("title") or "",
        listing.get("abstract") or "",
        listing.get("claims_text") or "",
    ]
    # Pre-set domain_tags + industry_tags are taxonomy keys; including their
    # *keywords* would be circular. We treat the existing tags as hard hits
    # later, not as text for classification.
    return " ".join(p for p in parts if p)


def _buyer_topical_text(buyer: Dict[str, Any]) -> str:
    """All buyer text that should feed topical/domain classification."""
    parts = [
        buyer.get("org_name") or "",
        buyer.get("industry") or "",
        buyer.get("use_cases") or "",
    ]
    for k in ("industries_of_interest", "technical_areas"):
        v = buyer.get(k)
        if isinstance(v, list):
            parts.append(" ".join(str(x) for x in v))
        elif v:
            parts.append(str(v))
    return " ".join(p for p in parts if p)


def extract_features(
    listing: Dict[str, Any],
    buyer: Dict[str, Any],
    retrieval_score: float,
) -> FeatureScores:
    """Compute the 6 cold-start features for one (listing, buyer) pair.

    Vocabulary alignment: we classify BOTH listing text and buyer text through
    the same _DOMAIN_KEYWORDS (innovation_scorer._classify_text), so the
    `domain_overlap` and `industry_match` features intersect non-empty sets
    instead of comparing a fixed-vocabulary tag against free-form XLSX text.

    Backward compat: any pre-set listing.domain_tags / industry_tags or
    buyer.technical_areas tokens that already match the taxonomy keys are
    merged into the classified set (a free-form 'machine learning' string and
    a pre-tagged 'ai_ml' token are treated as the same domain).
    """
    # Classify both sides via the canonical taxonomy
    listing_domains_inferred = _classify_via_innovation_scorer(_listing_topical_text(listing))
    buyer_domains_inferred = _classify_via_innovation_scorer(_buyer_topical_text(buyer))

    # Merge any pre-set taxonomy-key tokens (don't drop info)
    listing_domains_pre = _norm_tokens(listing.get("domain_tags"))
    buyer_domains_pre = _norm_tokens(buyer.get("technical_areas"))
    listing_domains = listing_domains_inferred | (listing_domains_pre & set(_TAXONOMY_KEYS()))
    buyer_domains = buyer_domains_inferred | (buyer_domains_pre & set(_TAXONOMY_KEYS()))

    if listing_domains and buyer_domains:
        domain_overlap = max(
            _jaccard_recall(listing_domains, buyer_domains),
            _jaccard_recall(buyer_domains, listing_domains),
        )
    else:
        domain_overlap = 0.0

    # ── KNOWN LIMITATION: industry_match is largely DORMANT in Phase 1 ──
    #
    # We deliberately reuse _DOMAIN_KEYWORDS (a research-domain taxonomy)
    # rather than invent a parallel industry taxonomy. Consequence: real
    # buyer industry strings - things like "Welding Automation & Robotics",
    # "Ophthalmology & Retinal Imaging", "Edge AI & TinyML" - don't
    # classify into any domain key, so industry_match scores 0.0 for
    # those buyers regardless of the listing.
    #
    # The feature is therefore "dormant, not misleading": it never wrongly
    # boosts the wrong buyer; it just contributes nothing for most
    # synthetic buyers we have today. The 5% weight it carries (see
    # ColdStartWeights) is small enough that this hurts ranking only
    # marginally.
    #
    # PHASE 2 FIX: when real buyers onboard, either (a) add a buyer-side
    # industry-to-domain mapping at profile-create time (the UI can offer
    # a taxonomy picklist alongside the free-form industry field), or (b)
    # introduce a parallel _INDUSTRY_KEYWORDS taxonomy. Don't tune
    # weights against the synthetic distribution.
    listing_industry_text = " ".join([
        listing.get("title") or "",
        " ".join(listing.get("industry_tags") or []),
    ])
    buyer_industry_text = " ".join([
        buyer.get("industry") or "",
        " ".join(buyer.get("industries_of_interest") or []),
    ])
    listing_industries = _classify_via_innovation_scorer(listing_industry_text)
    buyer_industries = _classify_via_innovation_scorer(buyer_industry_text)

    if listing_industries and buyer_industries:
        industry_match = max(
            _jaccard_recall(listing_industries, buyer_industries),
            _jaccard_recall(buyer_industries, listing_industries),
        )
    else:
        industry_match = 0.0

    return FeatureScores(
        retrieval=max(0.0, min(retrieval_score, 1.0)),
        domain_overlap=domain_overlap,
        industry_match=industry_match,
        recency=_recency_score(listing),
        maturity_match=_maturity_match(listing, buyer),
        budget_compat=_budget_compat(listing, buyer),
    )


def _TAXONOMY_KEYS() -> set:
    """Memoized list of valid domain keys from innovation_scorer."""
    try:
        from .innovation_scorer import _DOMAIN_KEYWORDS
        return set(_DOMAIN_KEYWORDS.keys())
    except Exception:
        return set()


# ─── Reranker ────────────────────────────────────────────────────────────

class MarketplaceReranker:
    """Cold-start blend now; LTR model swap-in later.

    The interface stays stable across both: rerank(features) -> score in 0-100.
    Phase 3 will check for a trained model file on init and use it instead.
    """

    def __init__(self, weights: Optional[ColdStartWeights] = None) -> None:
        self.weights = weights or ColdStartWeights()
        self.model = None  # populated in Phase 3
        self.mode = "cold_start"

    # ─── Scoring ─────────────────────────────────────────────────────────

    def score(self, features: FeatureScores) -> float:
        """Weighted blend, returns 0-100."""
        w = self._normalized_weights()
        total = (
            features.retrieval * w["retrieval"]
            + features.domain_overlap * w["domain_overlap"]
            + features.industry_match * w["industry_match"]
            + features.recency * w["recency"]
            + features.maturity_match * w["maturity_match"]
            + features.budget_compat * w["budget_compat"]
        )
        return round(min(total * 100, 100), 1)

    def score_pair(
        self,
        listing: Dict[str, Any],
        buyer: Dict[str, Any],
        retrieval_score: float,
    ) -> Tuple[float, FeatureScores, List[str]]:
        """Convenience: extract features + score + generate human reasons."""
        features = extract_features(listing, buyer, retrieval_score)
        score = self.score(features)
        reasons = self._reasons(features, listing, buyer)
        return score, features, reasons

    # ─── Diagnostics ─────────────────────────────────────────────────────

    def state(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "weights": self.weights.as_dict(),
            "model_loaded": self.model is not None,
        }

    # ─── Internals ───────────────────────────────────────────────────────

    def _normalized_weights(self) -> Dict[str, float]:
        d = self.weights.as_dict()
        total = sum(d.values()) or 1.0
        return {k: v / total for k, v in d.items()}

    @staticmethod
    def _reasons(features: FeatureScores, listing: Dict[str, Any],
                 buyer: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        if features.retrieval >= 0.6:
            out.append("Strong semantic similarity")
        if features.domain_overlap >= 0.5:
            shared = (set(_norm_tokens(listing.get("domain_tags")))
                      & set(_norm_tokens(buyer.get("technical_areas"))))
            if shared:
                out.append("Domain overlap: " + ", ".join(sorted(shared))[:120])
        if features.industry_match >= 0.5:
            out.append("Industry alignment")
        if features.recency >= 0.7:
            out.append("Recent patent (high recency)")
        if features.maturity_match >= 0.7:
            buyer_pref = buyer.get("tech_maturity_preference") or ""
            if buyer_pref:
                out.append(f"Fits maturity preference: {buyer_pref}")
        if features.budget_compat >= 0.9:
            out.append("Within budget band")
        if not out:
            out.append("Composite content match")
        return out


# ─── Future hook for Phase 3 LTR ─────────────────────────────────────────

class LearnedReranker(MarketplaceReranker):
    """Phase 3 placeholder. Will load a LightGBM LambdaMART model trained from
    marketplace_events grouped by query_hash, and replace `score` with model
    inference. Phase 1 leaves this as a no-op subclass."""

    def __init__(self, model_path: Optional[str] = None) -> None:
        super().__init__()
        self.mode = "ltr"
        self.model_path = model_path
        # TODO Phase 3: load LightGBM model from disk if available.


__all__ = [
    "MarketplaceReranker", "LearnedReranker",
    "ColdStartWeights", "FeatureScores", "extract_features",
]
