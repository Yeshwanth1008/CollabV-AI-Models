"""
CollabV AI - Patent Startup Opportunity Insights
====================================================
Given one patent listing, generates: potential startup ideas, a business
model suggestion, target industries, customer segments, revenue
opportunities, a commercialization roadmap, and a market-potential score.

Same Claude-first/rule-based-fallback shape as need_parser.py/resume_parser.py.
Cached via marketplace_db.py's marketplace_explanations table, keyed by
listing_id alone - a patent's commercialization potential doesn't depend on
which student is asking, and patent content rarely changes, so this is safe
to cache indefinitely (unlike skill-gap analysis, which is student-specific
and needs profile-change invalidation).
"""

from __future__ import annotations

import os
import re
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import marketplace_db as mdb


@dataclass
class StartupInsights:
    startup_ideas: List[str] = field(default_factory=list)
    business_model: str = ""
    target_industries: List[str] = field(default_factory=list)
    customer_segments: List[str] = field(default_factory=list)
    revenue_opportunities: List[str] = field(default_factory=list)
    commercialization_roadmap: List[str] = field(default_factory=list)
    market_potential_score: float = 0.0
    source: str = "rule"  # "claude" | "rule" | "cache"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "startup_ideas": self.startup_ideas,
            "business_model": self.business_model,
            "target_industries": self.target_industries,
            "customer_segments": self.customer_segments,
            "revenue_opportunities": self.revenue_opportunities,
            "commercialization_roadmap": self.commercialization_roadmap,
            "market_potential_score": self.market_potential_score,
            "source": self.source,
        }


STARTUP_INSIGHTS_PROMPT = """You are a startup/commercialization advisor evaluating a \
university patent for entrepreneurial potential.

Patent:
{patent}

Return ONLY valid JSON with these fields:
{{
  "startup_ideas": ["2-4 concrete startup concepts built around this patent"],
  "business_model": "a 1-2 sentence business model suggestion (e.g. B2B SaaS, licensing, hardware sales)",
  "target_industries": ["2-5 industries this technology could serve"],
  "customer_segments": ["2-4 customer segment descriptions"],
  "revenue_opportunities": ["2-4 revenue stream ideas"],
  "commercialization_roadmap": ["3-5 ordered milestones from prototype to market"],
  "market_potential_score": integer 0-100, overall commercial potential
}}"""


def _generate_with_claude(listing: Dict[str, Any]) -> Optional[StartupInsights]:
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None

        client = anthropic.Anthropic(api_key=api_key)
        patent_summary = json.dumps({
            "title": listing.get("title", ""),
            "abstract": listing.get("abstract", ""),
            "domain_tags": listing.get("domain_tags", []),
            "industry_tags": listing.get("industry_tags", []),
            "status": listing.get("status", ""),
        })

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": STARTUP_INSIGHTS_PROMPT.format(patent=patent_summary),
            }],
        )

        content = response.content[0].text
        json_match = re.search(r"\{[\s\S]*\}", content)
        if not json_match:
            return None
        data = json.loads(json_match.group())

        return StartupInsights(
            startup_ideas=data.get("startup_ideas", []),
            business_model=data.get("business_model", ""),
            target_industries=data.get("target_industries", []),
            customer_segments=data.get("customer_segments", []),
            revenue_opportunities=data.get("revenue_opportunities", []),
            commercialization_roadmap=data.get("commercialization_roadmap", []),
            market_potential_score=float(data.get("market_potential_score", 0)),
            source="claude",
        )
    except Exception:
        return None


_ROADMAP_TEMPLATE = [
    "Validate the technology with pilot customers in the target industry",
    "Build a minimum viable product / proof of concept",
    "File for licensing terms with the university technology transfer office",
    "Secure early customers or seed funding",
    "Scale manufacturing/deployment and expand market reach",
]


def _generate_rule_based(listing: Dict[str, Any]) -> StartupInsights:
    domains = [str(d).replace("_", " ") for d in (listing.get("domain_tags") or [])]
    industries = [str(i) for i in (listing.get("industry_tags") or [])] or (domains[:2] or ["General Technology"])
    title = listing.get("title", "this technology")

    return StartupInsights(
        startup_ideas=[
            f"A startup commercializing {title.lower()} for {ind.lower()} applications"
            for ind in industries[:3]
        ] or [f"A startup built around {title}"],
        business_model="Licensing to established players, or direct-to-market hardware/software sales" if domains else "Licensing or direct commercialization, depending on target market",
        target_industries=industries[:5],
        customer_segments=[f"{ind} companies seeking new {domains[0] if domains else 'technology'} capabilities" for ind in industries[:3]] or ["Early adopters in adjacent industries"],
        revenue_opportunities=["Patent licensing fees", "Direct product/service sales", "Consulting and integration services"],
        commercialization_roadmap=_ROADMAP_TEMPLATE,
        market_potential_score=60.0 if industries else 40.0,
        source="rule",
    )


def generate_startup_insights(
    listing: Dict[str, Any],
    use_claude: bool = True,
    db_path: Optional[str] = None,
) -> StartupInsights:
    listing_id = listing.get("listing_id", "")
    cache_key = f"startup_insights:{listing_id}"

    cached = mdb.get_explanation(cache_key, db_path)
    if cached:
        result = StartupInsights(**cached["explanation"])
        result.source = "cache"
        return result

    result = None
    if use_claude:
        result = _generate_with_claude(listing)
    if result is None:
        result = _generate_rule_based(listing)

    mdb.save_explanation(cache_key, "startup_insights", "patent", listing_id, result.to_dict(), db_path)
    return result


__all__ = ["StartupInsights", "generate_startup_insights"]
