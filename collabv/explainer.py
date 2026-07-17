"""
CollabV AI - LLM-Powered Explainable Match Reasons
======================================================
Replaces the rule-based `reasons` strings on each match with a structured,
human-readable explanation. Uses the Claude API when available, falls back to
template-based rendering otherwise. Caches results in SQLite to avoid
re-paying for the same (professor, request) pair within 7 days.

Public API:
    MatchExplainer().explain_match(prof, request, scores) -> MatchExplanation
    MatchExplainer().explain_batch(matches, professors_by_id, request, top_k=5)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


# ─── Data classes ───────────────────────────────────────────────────────────

@dataclass
class MatchExplanation:
    professor_id: str
    summary: str
    key_strengths: List[str] = field(default_factory=list)
    potential_gaps: List[str] = field(default_factory=list)
    suggested_talking_points: List[str] = field(default_factory=list)
    confidence: str = "good match"          # strong | good | exploratory
    source: str = "rule"                    # "claude" | "rule" | "cache"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Cache table ────────────────────────────────────────────────────────────

_CACHE_TTL_SECONDS = 7 * 24 * 3600


def init_explanation_cache(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS match_explanations (
                cache_key TEXT PRIMARY KEY,
                professor_id TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                explanation_json TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ─── Explainer ──────────────────────────────────────────────────────────────

class MatchExplainer:
    """Generate human-readable explanations for professor-company matches."""

    def __init__(
        self,
        db_path: Optional[str] = None,
        use_claude: bool = True,
        api_key: Optional[str] = None,
        model: str = "claude-sonnet-4-6",
        timeout_seconds: float = 45.0,
    ) -> None:
        self.db_path = db_path
        self.use_claude = use_claude
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        self.timeout = timeout_seconds
        if db_path:
            try:
                init_explanation_cache(db_path)
            except Exception as e:
                logger.warning("Failed to init explanation cache: %s", e)

    # ─── Public ─────────────────────────────────────────────────────────────

    def explain_match(
        self,
        professor: Dict[str, Any],
        request: Any,
        scores: Optional[Dict[str, Any]] = None,
    ) -> MatchExplanation:
        prof_id = str(professor.get("professor_id", ""))
        request_hash = self._hash_request(request)
        cache_key = f"{prof_id}:{request_hash}"

        # Cache check
        cached = self._cache_get(cache_key)
        if cached:
            return cached

        explanation = None
        if self.use_claude and self.api_key:
            try:
                explanation = self._generate_with_claude(professor, request, scores or {})
            except Exception as e:
                logger.warning("Claude explanation failed for %s: %s", prof_id, e)
                explanation = None

        if explanation is None:
            explanation = self._generate_rule_based(professor, request, scores or {})

        explanation.professor_id = prof_id
        self._cache_put(cache_key, prof_id, request_hash, explanation)
        return explanation

    def explain_batch(
        self,
        matches: Sequence[Dict[str, Any]],
        professors_by_id: Dict[str, Dict[str, Any]],
        request: Any,
        top_k: int = 5,
    ) -> List[MatchExplanation]:
        results: List[MatchExplanation] = []
        for m in matches[:top_k]:
            pid = str(m.get("professor_id", ""))
            prof = professors_by_id.get(pid, {})
            if not prof:
                prof = {
                    "professor_id": pid,
                    "name": m.get("professor_name", ""),
                    "department": m.get("department", ""),
                }
            results.append(self.explain_match(prof, request, m))
        return results

    # ─── Claude path ────────────────────────────────────────────────────────

    def _generate_with_claude(
        self,
        professor: Dict[str, Any],
        request: Any,
        scores: Dict[str, Any],
    ) -> Optional[MatchExplanation]:
        try:
            import anthropic
        except ImportError:
            logger.info("anthropic SDK not installed; skipping LLM explanation")
            return None

        client = anthropic.Anthropic(api_key=self.api_key, timeout=self.timeout)

        system_prompt = (
            "You are CollabV AI, a professor-company matching platform. "
            "You write concise, honest explanations for why a specific professor "
            "is recommended for a specific company request. "
            "Write from the platform's perspective. Cite actual research areas, "
            "publication topics, or patent domains. Acknowledge gaps honestly. "
            "Keep summary to 2-3 sentences. Return STRICT JSON only."
        )

        user_prompt = self._build_user_prompt(professor, request, scores)

        msg = client.messages.create(
            model=self.model,
            max_tokens=800,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = ""
        try:
            text = msg.content[0].text
        except Exception:
            return None

        # Find JSON block
        import re
        json_match = re.search(r"\{[\s\S]*\}", text)
        if not json_match:
            return None
        try:
            data = json.loads(json_match.group())
        except json.JSONDecodeError:
            return None

        score = float(scores.get("score", 0) or 0)
        confidence = "strong match" if score >= 80 else "good match" if score >= 60 else "exploratory match"

        return MatchExplanation(
            professor_id=str(professor.get("professor_id", "")),
            summary=str(data.get("summary", "")).strip(),
            key_strengths=[str(s).strip() for s in data.get("key_strengths", [])][:5],
            potential_gaps=[str(s).strip() for s in data.get("potential_gaps", [])][:4],
            suggested_talking_points=[str(s).strip() for s in data.get("suggested_talking_points", [])][:5],
            confidence=confidence,
            source="claude",
        )

    @staticmethod
    def _build_user_prompt(prof: Dict[str, Any], request: Any, scores: Dict[str, Any]) -> str:
        # Professor summary
        prof_summary = {
            "name": prof.get("name", ""),
            "department": prof.get("department", ""),
            "research_areas": prof.get("research_areas", [])[:8],
            "expertise": prof.get("technical_expertise", [])[:10],
            "publications_sample": [str(p) for p in (prof.get("publications") or [])[:3]],
            "patent_count": len(prof.get("patents") or []),
            "seniority": prof.get("seniority_level", ""),
        }

        # Request summary
        getter = lambda k: getattr(request, k, None) if not isinstance(request, dict) else request.get(k)
        req_summary = {
            "industry": getter("industry"),
            "technical_area": getter("technical_area"),
            "required_expertise": getter("required_expertise"),
            "project_description": getter("project_description"),
            "collaboration_type": getter("collaboration_type"),
            "timeline_months": getter("timeline_months"),
        }

        # Scores summary
        sc_summary = {k: v for k, v in scores.items() if isinstance(v, (int, float))}

        return f"""Generate an explanation for this match in STRICT JSON format:

{{
  "summary": "2-3 sentences explaining why this professor is recommended for this company",
  "key_strengths": ["3 bullet points - specific, citing real research/patents"],
  "potential_gaps": ["1-3 honest gaps or weaknesses"],
  "suggested_talking_points": ["3 conversation starters for the first call"]
}}

Professor:
{json.dumps(prof_summary, ensure_ascii=False, default=str)}

Company need:
{json.dumps(req_summary, ensure_ascii=False, default=str)}

Score breakdown:
{json.dumps(sc_summary, ensure_ascii=False, default=str)}

Return ONLY the JSON, no preamble or trailing text.
"""

    # ─── Rule-based fallback ────────────────────────────────────────────────

    @staticmethod
    def _generate_rule_based(
        professor: Dict[str, Any],
        request: Any,
        scores: Dict[str, Any],
    ) -> MatchExplanation:
        name = professor.get("name", "the professor")
        dept = (professor.get("department") or "").replace("Department of ", "")
        score = float(scores.get("score", 0) or 0)
        research = professor.get("research_areas") or []
        expertise = professor.get("technical_expertise") or []
        reasons = scores.get("reasons") or []

        getter = lambda k: getattr(request, k, None) if not isinstance(request, dict) else request.get(k)
        industry = getter("industry") or "your project"
        req_tech = getter("technical_area") or []

        # Identify top factor
        factors = [
            ("research alignment", float(scores.get("tier1_score", 0) or 0)),
            ("semantic similarity", float(scores.get("tier2_score", 0) or 0)),
            ("readiness", float(scores.get("readiness_score", 0) or 0)),
            ("patent portfolio", float(scores.get("patent_score", 0) or 0)),
        ]
        factors.sort(key=lambda x: -x[1])
        top_factor = factors[0]

        summary = (
            f"Dr. {name} ({dept}) scores {score:.0f}/100 for your {industry} engagement. "
        )
        if research:
            summary += f"Their core research in {', '.join(str(r) for r in research[:2])} aligns with what you need. "
        else:
            summary += f"Their strongest dimension is {top_factor[0]} ({top_factor[1]:.0f}/100). "

        strengths = []
        if reasons:
            strengths.extend(str(r) for r in reasons[:3])
        if research:
            strengths.append(f"Research focus: {', '.join(str(r) for r in research[:3])}")
        if expertise:
            strengths.append(f"Technical expertise: {', '.join(str(e) for e in expertise[:4])}")
        patents = professor.get("patents") or []
        if patents:
            strengths.append(f"Active patent portfolio ({len(patents)} filings)")

        gaps = []
        if score < 65:
            gaps.append("Composite match score is moderate - validate fit in initial call")
        coll_hist = professor.get("collaboration_history")
        if not coll_hist:
            gaps.append("No explicit industry collaboration history on file")
        timeline = getter("timeline_months") or 0
        if isinstance(timeline, (int, float)) and 0 < timeline < 6:
            gaps.append(f"Project timeline of {timeline} months is shorter than typical academic engagements")

        # Talking points
        talking_points = []
        if research:
            talking_points.append(f"Ask about recent work in {research[0]}")
        if req_tech and isinstance(req_tech, list) and req_tech:
            talking_points.append(f"Explore overlap between their research and {req_tech[0]}")
        if patents:
            talking_points.append("Discuss whether existing IP can accelerate the project")
        if not talking_points:
            talking_points.append("Start with an introduction call to scope objectives")

        confidence = "strong match" if score >= 80 else "good match" if score >= 60 else "exploratory match"

        return MatchExplanation(
            professor_id=str(professor.get("professor_id", "")),
            summary=summary.strip(),
            key_strengths=strengths[:4],
            potential_gaps=gaps[:3],
            suggested_talking_points=talking_points[:4],
            confidence=confidence,
            source="rule",
        )

    # ─── Cache ──────────────────────────────────────────────────────────────

    def _cache_get(self, cache_key: str) -> Optional[MatchExplanation]:
        if not self.db_path:
            return None
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                row = conn.execute(
                    "SELECT explanation_json, created_at FROM match_explanations WHERE cache_key = ?",
                    (cache_key,),
                ).fetchone()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Cache read failed: %s", e)
            return None
        if not row:
            return None
        if time.time() - float(row[1]) > _CACHE_TTL_SECONDS:
            return None
        try:
            data = json.loads(row[0])
            explanation = MatchExplanation(**data)
            explanation.source = "cache"
            return explanation
        except Exception:
            return None

    def _cache_put(
        self,
        cache_key: str,
        professor_id: str,
        request_hash: str,
        explanation: MatchExplanation,
    ) -> None:
        if not self.db_path:
            return
        try:
            conn = sqlite3.connect(self.db_path)
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO match_explanations
                       (cache_key, professor_id, request_hash, explanation_json, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        cache_key, professor_id, request_hash,
                        json.dumps(explanation.to_dict()),
                        time.time(),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.debug("Cache write failed: %s", e)

    @staticmethod
    def _hash_request(request: Any) -> str:
        if request is None:
            return "empty"
        if isinstance(request, dict):
            data = request
        else:
            data = {
                k: getattr(request, k, None) for k in (
                    "industry", "technical_area", "required_expertise",
                    "project_description", "challenges", "collaboration_type",
                )
            }
        blob = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


__all__ = ["MatchExplainer", "MatchExplanation", "init_explanation_cache"]
