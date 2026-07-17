"""
CollabV AI - Knowledge Graph (NetworkX-based, lightweight Neo4j substitute)
============================================================================
Per the spec's Layer 2: a graph of professors, skills, domains, departments,
and industries with edges representing relationships. We use NetworkX
in-memory (no separate database) so it deploys with the backend and stays
fast at our scale (~5,000 nodes, ~30,000 edges).

Public API:
    KnowledgeGraph(professors).build()
    kg.domain_overlap_score(prof_id, request_domains) -> 0..1
    kg.shortest_path(prof_id, target_domain) -> list[str]
    kg.related_professors(prof_id, max_hops=2) -> list[(prof_id, distance)]
    kg.industry_bridge(industry_a, industry_b) -> list[prof_id]
    kg.export(path) / kg.load(path)
    kg.stats() -> dict
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# NetworkX is lightweight; we hard-require it for this module but the matching
# engine catches ImportError and treats KG as optional.
try:
    import networkx as nx
except ImportError:                                  # pragma: no cover
    nx = None  # type: ignore


# ─── Node-type prefixes ───────────────────────────────────────────────────

P = "prof:"     # professor
S = "skill:"    # skill / expertise term
D = "domain:"   # research domain (high-level)
DEPT = "dept:"  # academic department
I = "ind:"      # industry tag


# Edge relationship types
EDGE_HAS_SKILL    = "HAS_SKILL"
EDGE_IN_DOMAIN    = "IN_DOMAIN"
EDGE_IN_DEPT      = "IN_DEPT"
EDGE_TOUCHES_IND  = "TOUCHES_INDUSTRY"
EDGE_COLLAB_HIST  = "COLLAB_HISTORY"
EDGE_RELATED      = "RELATED_DOMAIN"


# Curated cross-domain bridges (same relationships the innovation scorer rewards)
_DOMAIN_RELATIONSHIPS: List[Tuple[str, str]] = [
    ("ai_ml", "robotics"), ("ai_ml", "healthcare"), ("ai_ml", "materials"),
    ("ai_ml", "biotech"),  ("ai_ml", "energy"),    ("ai_ml", "data_science"),
    ("robotics", "manufacturing"), ("robotics", "healthcare"),
    ("energy", "materials"),       ("energy", "chemicals"),
    ("biotech", "chemicals"),      ("biotech", "healthcare"),
    ("materials", "manufacturing"),("materials", "aerospace"),
    ("sensors_iot", "healthcare"), ("sensors_iot", "civil"),
    ("optics", "electronics"),
]


def _norm_token(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", str(text).lower()).strip()


def _normalize_dept(text: str) -> str:
    return _norm_token(text.replace("Department of ", ""))


# ─── Knowledge graph ──────────────────────────────────────────────────────

class KnowledgeGraph:
    """Build + query an in-memory professor / domain / skill graph."""

    def __init__(self, professors: Sequence[Dict[str, Any]]) -> None:
        if nx is None:
            raise RuntimeError(
                "networkx is not installed. pip install networkx>=3.0"
            )
        self.professors = list(professors)
        self.graph = nx.Graph()

    # ─── Build ────────────────────────────────────────────────────────────

    def build(self) -> "KnowledgeGraph":
        """Populate nodes + edges from the professor records."""
        from .innovation_scorer import _DOMAIN_KEYWORDS, _classify_text

        # Domain nodes + curated domain-domain bridges
        for d in _DOMAIN_KEYWORDS:
            self.graph.add_node(D + d, type="domain")
        for a, b in _DOMAIN_RELATIONSHIPS:
            self.graph.add_edge(D + a, D + b, rel=EDGE_RELATED, weight=2.0)

        for p in self.professors:
            pid = str(p.get("professor_id", ""))
            if not pid:
                continue
            self.graph.add_node(
                P + pid, type="professor",
                name=p.get("name", ""),
                department=p.get("department", ""),
            )

            # department -> professor
            dept = _normalize_dept(p.get("department", ""))
            if dept:
                self.graph.add_node(DEPT + dept, type="department")
                self.graph.add_edge(P + pid, DEPT + dept, rel=EDGE_IN_DEPT,
                                    weight=1.0)

            # skills -> professor
            skills = (p.get("technical_expertise") or []) + \
                     (p.get("matching_tags", {}).get("tech_skill_tags", []) or [])
            for skill in skills:
                tok = _norm_token(skill)
                if not tok or len(tok) < 3:
                    continue
                self.graph.add_node(S + tok, type="skill")
                self.graph.add_edge(P + pid, S + tok, rel=EDGE_HAS_SKILL,
                                    weight=1.0)

            # research areas + biography -> domains
            text_for_domain = " ".join([
                p.get("biography", "") or "",
                " ".join(p.get("research_areas", []) or []),
                " ".join(p.get("technical_expertise", []) or []),
            ])
            for domain in _classify_text(text_for_domain):
                self.graph.add_edge(P + pid, D + domain, rel=EDGE_IN_DOMAIN,
                                    weight=1.0)

            # industry exposure + collaboration history -> industry nodes
            industries = []
            ind_tags = p.get("matching_tags", {}).get("industry_tags", []) or []
            industries.extend(ind_tags)
            indu = p.get("industry_exposure")
            if isinstance(indu, list):
                industries.extend(indu)
            elif isinstance(indu, str):
                industries.append(indu)

            for ind in industries:
                tok = _norm_token(ind)
                if not tok or len(tok) < 3:
                    continue
                self.graph.add_node(I + tok, type="industry")
                self.graph.add_edge(P + pid, I + tok, rel=EDGE_TOUCHES_IND,
                                    weight=1.0)

            # collaboration history acts as a stronger industry edge
            if p.get("collaboration_history"):
                self.graph.add_edge(
                    P + pid, I + "industry_collab",
                    rel=EDGE_COLLAB_HIST, weight=2.0,
                )

        logger.info("KG built: %d nodes, %d edges",
                    self.graph.number_of_nodes(),
                    self.graph.number_of_edges())
        return self

    # ─── Queries ──────────────────────────────────────────────────────────

    def domain_overlap_score(
        self, professor_id: str, request_domains: Iterable[str]
    ) -> float:
        """0..1 score of how well professor's domain set covers the requested
        domains. Direct hit = 1.0; reachable in 1 hop = 0.5; 2 hops = 0.2.
        """
        prof_node = P + str(professor_id)
        if not self.graph.has_node(prof_node):
            return 0.0
        # Domains this professor is directly attached to
        prof_domains: set = set()
        for n in self.graph.neighbors(prof_node):
            if n.startswith(D):
                prof_domains.add(n[len(D):])

        if not prof_domains:
            return 0.0

        req = [d for d in request_domains if d]
        if not req:
            return 0.0

        score = 0.0
        for r in req:
            if r in prof_domains:
                score += 1.0
                continue
            # 1-hop via curated domain-relationship edges
            r_node = D + r
            if self.graph.has_node(r_node):
                neighbors = {n[len(D):] for n in self.graph.neighbors(r_node)
                             if n.startswith(D)}
                if neighbors & prof_domains:
                    score += 0.5
                    continue
                # 2-hop expansion
                two_hop = set()
                for nb in self.graph.neighbors(r_node):
                    if nb.startswith(D):
                        for nb2 in self.graph.neighbors(nb):
                            if nb2.startswith(D):
                                two_hop.add(nb2[len(D):])
                if two_hop & prof_domains:
                    score += 0.2

        return min(score / max(len(req), 1), 1.0)

    def shortest_path(
        self, professor_id: str, target: str, target_type: str = "domain",
    ) -> List[str]:
        """Shortest path from a professor to a domain/skill/industry node."""
        prof_node = P + str(professor_id)
        prefix = {"domain": D, "skill": S, "industry": I, "dept": DEPT}[target_type]
        target_node = prefix + _norm_token(target.replace("Department of ", ""))
        if not self.graph.has_node(prof_node) or not self.graph.has_node(target_node):
            return []
        try:
            return nx.shortest_path(self.graph, prof_node, target_node)
        except nx.NetworkXNoPath:
            return []

    def related_professors(
        self, professor_id: str, max_hops: int = 2,
    ) -> List[Tuple[str, int]]:
        """Professors reachable from this one within max_hops via shared
        skills/domains/departments. Returns [(professor_id, distance), ...]
        sorted by distance ascending.
        """
        prof_node = P + str(professor_id)
        if not self.graph.has_node(prof_node):
            return []
        results: Dict[str, int] = {}
        # BFS to max_hops, collecting professor neighbors
        for target, distance in nx.single_source_shortest_path_length(
            self.graph, prof_node, cutoff=max_hops
        ).items():
            if target.startswith(P) and target != prof_node:
                results[target[len(P):]] = distance
        return sorted(results.items(), key=lambda x: x[1])

    def industry_bridge(
        self, industry_a: str, industry_b: str,
    ) -> List[str]:
        """Professors that touch BOTH industries — candidates for any
        company sitting at that intersection."""
        a_node = I + _norm_token(industry_a)
        b_node = I + _norm_token(industry_b)
        if not (self.graph.has_node(a_node) and self.graph.has_node(b_node)):
            return []
        profs_a = {n[len(P):] for n in self.graph.neighbors(a_node)
                   if n.startswith(P)}
        profs_b = {n[len(P):] for n in self.graph.neighbors(b_node)
                   if n.startswith(P)}
        return sorted(profs_a & profs_b)

    def top_skills(self, professor_id: str, n: int = 8) -> List[str]:
        prof_node = P + str(professor_id)
        if not self.graph.has_node(prof_node):
            return []
        skills = [n[len(S):] for n in self.graph.neighbors(prof_node)
                  if n.startswith(S)]
        return sorted(skills)[:n]

    def stats(self) -> Dict[str, Any]:
        types: Dict[str, int] = {}
        for _, data in self.graph.nodes(data=True):
            t = data.get("type", "unknown")
            types[t] = types.get(t, 0) + 1
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "by_type": types,
        }

    # ─── Persistence ──────────────────────────────────────────────────────

    def export(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = nx.node_link_data(self.graph, edges="links")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)

    def load(self, path: str) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        self.graph = nx.node_link_graph(data, edges="links")
        return True


__all__ = [
    "KnowledgeGraph",
    "P", "S", "D", "DEPT", "I",
    "EDGE_HAS_SKILL", "EDGE_IN_DOMAIN", "EDGE_IN_DEPT",
    "EDGE_TOUCHES_IND", "EDGE_COLLAB_HIST", "EDGE_RELATED",
]
