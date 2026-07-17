"""
LLM-generated match explanations.

One batched Claude call per search request (not one call per result) — the
prompt lists every candidate's already-computed matched signals and asks for
a short grounded sentence per candidate, returned as JSON. Grounding in
precomputed signals (matched_skills, matched_research_areas, scores) rather
than raw profile text keeps the model from inventing matches that don't
exist. Falls back to a deterministic template if there's no API key, the
call fails, or the response doesn't parse — explanations must never be a
single point of failure for search.
"""
import json
import logging

from anthropic import Anthropic

from .config import get_settings

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic | None:
    global _client
    settings = get_settings()
    if not settings.anthropic_api_key:
        return None
    if _client is None:
        _client = Anthropic(api_key=settings.anthropic_api_key)
    return _client


def build_template_explanation(c: dict) -> str:
    bits = []
    if c.get("matched_skills"):
        bits.append(f"matches skills {', '.join(c['matched_skills'][:3])}")
    if c.get("matched_research_areas"):
        bits.append(f"aligned research in {', '.join(c['matched_research_areas'][:3])}")
    if not bits and c.get("semantic", 0) >= 0.55:
        bits.append("semantically similar profile based on overall background")
    if not bits and c.get("keyword", 0) >= 0.4:
        bits.append("strong keyword overlap with the query")
    if not bits:
        bits.append("partial relevance to the query")
    role = c.get("role", "profile")
    return f"{c.get('name', 'This ' + role)} is a {role} that {'; '.join(bits)}."


PROMPT_TEMPLATE = """You write one-sentence, factual explanations of why each candidate profile \
matched a search query on a professional/academic networking platform. \
Ground every sentence ONLY in the signals given for that candidate — never invent skills, \
publications, or facts not listed. Be specific and concise (max 25 words per explanation).

Search query: "{query}"
Expanded/related terms considered: {expanded_terms}

Candidates (JSON):
{candidates_json}

Return ONLY a JSON object mapping each candidate "id" to its explanation string. No prose, no markdown fences."""


def generate_explanations_batch(query: str, expanded_terms: list[str], candidates: list[dict]) -> dict[str, str]:
    """
    candidates: [{id, name, role, headline, matched_skills, matched_research_areas,
                  semantic, keyword, rerank}]
    Returns {id: explanation}.
    """
    if not candidates:
        return {}

    client = _get_client()
    if client is None:
        return {c["id"]: build_template_explanation(c) for c in candidates}

    settings = get_settings()
    payload = [
        {
            "id": c["id"],
            "name": c.get("name"),
            "role": c.get("role"),
            "headline": c.get("headline"),
            "matched_skills": c.get("matched_skills", []),
            "matched_research_areas": c.get("matched_research_areas", []),
            "semantic_similarity": round(c.get("semantic", 0.0), 3),
            "keyword_score": round(c.get("keyword", 0.0), 3),
            "rerank_score": round(c.get("rerank", 0.0), 3),
        }
        for c in candidates
    ]
    prompt = PROMPT_TEMPLATE.format(
        query=query,
        expanded_terms=", ".join(expanded_terms) if expanded_terms else "(none)",
        candidates_json=json.dumps(payload, indent=2),
    )

    try:
        resp = client.messages.create(
            model=settings.llm_model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
            timeout=8.0,
        )
        text = "".join(block.text for block in resp.content if block.type == "text").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):]
        parsed = json.loads(text)
        result = {}
        for c in candidates:
            cid = c["id"]
            result[cid] = parsed.get(cid) or build_template_explanation(c)
        return result
    except Exception:
        logger.exception("LLM explanation generation failed, falling back to templates")
        return {c["id"]: build_template_explanation(c) for c in candidates}
