"""
CollabV AI - Marketplace deterministic business-rule layer.

Hard filters applied AFTER retrieval and BEFORE rerank. These rules express
business logic that must hold regardless of how good a learned model thinks
a match is.

The spec requires:
  - STUDENT users are excluded from inventor-initiated proposal recommendations
    (Mode A). Students can still browse and inquire (Mode B / browse).
  - Only ACTIVE listings are visible in Mode B and to guest browse.
  - Drafts and pending_approval are inventor-private.

Other rules are configurable via MarketplaceRulesConfig and can be turned
off for offline evaluation runs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from . import marketplace_db as mdb
from .auth import (
    ROLE_ADMIN,
    ROLE_PROFESSOR_USER,
    ROLE_STUDENT_USER,
    ROLE_BUYER_USER,
    ROLE_COMPANY_USER,
)


# ─── Config ───────────────────────────────────────────────────────────────

@dataclass
class MarketplaceRulesConfig:
    """Toggles for the deterministic filter layer. Defaults are production-safe."""

    # Mode A: candidate buyers for an inventor's patent
    # Reversed 2026: the Patent Marketplace feature explicitly lets professors
    # offer patents to students (a first-class audience type alongside
    # companies/employees/professors/institutes), so students are no longer
    # excluded from inventor-initiated proposals.
    exclude_students_from_proposals: bool = False
    exclude_synthetic_buyers_from_proposals: bool = True
    exclude_own_institution_buyers: bool = True   # buyer @ same university as inventor
    enforce_geographic_scope: bool = False         # off until we collect this data

    # Mode B: recommended patents for a buyer
    only_public_listings_in_browse: bool = True
    exclude_inventor_own_listings: bool = True     # buyer == professor case
    respect_budget_ceiling: bool = False           # off; ranges are coarse for now

    # Cross-mode
    blocked_buyer_ids: Set[str] = field(default_factory=set)
    blocked_listing_ids: Set[str] = field(default_factory=set)


# ─── Helpers ──────────────────────────────────────────────────────────────

def _user_role(user_id: Optional[str], user_lookup: Dict[str, Dict[str, Any]]) -> str:
    """Best-effort role resolution. Returns '' if user not found / not loaded."""
    if not user_id:
        return ""
    u = user_lookup.get(user_id)
    if not u:
        return ""
    return (u.get("role") or "").lower()


# ─── Mode A: filter candidate buyers ─────────────────────────────────────

def filter_candidate_buyers(
    candidates: List[Dict[str, Any]],
    listing: Dict[str, Any],
    user_lookup: Dict[str, Dict[str, Any]],
    professor_lookup: Optional[Dict[str, Dict[str, Any]]] = None,
    config: Optional[MarketplaceRulesConfig] = None,
) -> List[Dict[str, Any]]:
    """Remove ineligible buyers for an inventor-initiated proposal.

    Args:
        candidates: list of buyer dicts (with 'buyer_id', 'user_id', etc.)
        listing: the patent listing dict that we're proposing from
        user_lookup: map of user_id -> users-table row
        professor_lookup: map of professor_id -> professor profile (for
            same-institution checks)
        config: rule toggles
    """
    cfg = config or MarketplaceRulesConfig()
    inventor_id = listing.get("professor_id")
    inventor_profile = (professor_lookup or {}).get(inventor_id) or {}
    inventor_university = (inventor_profile.get("university") or "").lower()

    out: List[Dict[str, Any]] = []
    for b in candidates:
        buyer_id = b.get("buyer_id")
        if buyer_id in cfg.blocked_buyer_ids:
            continue

        # Hard rule per spec: students excluded from proposal targets
        if cfg.exclude_students_from_proposals:
            role = _user_role(b.get("user_id"), user_lookup)
            if role == ROLE_STUDENT_USER:
                continue

        if cfg.exclude_synthetic_buyers_from_proposals and b.get("is_synthetic"):
            continue

        if cfg.exclude_own_institution_buyers and inventor_university:
            buyer_user = user_lookup.get(b.get("user_id"), {})
            buyer_company = (buyer_user.get("company_name") or "").lower()
            # Coarse match: buyer's company name contains inventor's university
            if inventor_university and inventor_university in buyer_company:
                continue

        out.append(b)
    return out


# ─── Mode B: filter candidate patents ────────────────────────────────────

def filter_candidate_patents(
    candidates: List[Dict[str, Any]],
    buyer: Optional[Dict[str, Any]],
    config: Optional[MarketplaceRulesConfig] = None,
) -> List[Dict[str, Any]]:
    """Remove ineligible patent listings for a buyer's recommendation list."""
    cfg = config or MarketplaceRulesConfig()
    buyer_user_id = (buyer or {}).get("user_id")

    out: List[Dict[str, Any]] = []
    for l in candidates:
        listing_id = l.get("listing_id")
        if listing_id in cfg.blocked_listing_ids:
            continue

        if cfg.only_public_listings_in_browse:
            if l.get("status") not in mdb.PUBLIC_LISTING_STATES:
                continue

        # If the buyer is also a professor (dual-role), hide their own listings
        if cfg.exclude_inventor_own_listings and buyer_user_id:
            # We don't have direct user_id on a listing, but we can match
            # listing.professor_id ↔ a known mapping in the engine layer.
            # Phase 1: the engine passes an `own_professor_id` hint when applicable.
            own = (buyer or {}).get("_own_professor_id")
            if own and l.get("professor_id") == own:
                continue

        out.append(l)
    return out


# ─── Guest browse ────────────────────────────────────────────────────────

def filter_browse_listings(
    listings: List[Dict[str, Any]],
    config: Optional[MarketplaceRulesConfig] = None,
) -> List[Dict[str, Any]]:
    """For unauthenticated browse, only show publicly-visible listings."""
    cfg = config or MarketplaceRulesConfig()
    if not cfg.only_public_listings_in_browse:
        return listings
    return [l for l in listings if l.get("status") in mdb.PUBLIC_LISTING_STATES]


__all__ = [
    "MarketplaceRulesConfig",
    "filter_candidate_buyers",
    "filter_candidate_patents",
    "filter_browse_listings",
]
