"""
Hybrid search engine combining BM25 + TF-IDF + Name matching.
Returns ranked professor matches with confidence scores.
"""

import difflib
import json
import pickle
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

BASE_DIR = Path(__file__).resolve().parent


@dataclass
class SearchResult:
    """A single search result with confidence scoring."""
    index: int
    name: str
    department: str
    designation: str
    confidence: float
    name_score: float = 0.0
    bm25_score: float = 0.0
    tfidf_score: float = 0.0
    match_type: str = ""
    research_areas: list = field(default_factory=list)

    def to_dict(self):
        return {
            "index": int(self.index),
            "name": self.name,
            "department": self.department,
            "designation": self.designation,
            "confidence": round(float(self.confidence), 4),
            "name_score": round(float(self.name_score), 4),
            "bm25_score": round(float(self.bm25_score), 4),
            "tfidf_score": round(float(self.tfidf_score), 4),
            "match_type": self.match_type,
            "research_areas": self.research_areas,
        }


class HybridSearchEngine:
    """
    Hybrid search combining BM25 keyword search + TF-IDF semantic search + name matching.
    """

    def __init__(self):
        self.professors = []
        self.bm25 = None
        self.tokenized_corpus = []
        self.tfidf_vectorizer = None
        self.tfidf_matrix = None
        self.name_variants = {}
        self._loaded = False

    def load_indexes(self):
        """Load all pre-built indexes from disk."""
        if self._loaded:
            return

        # Load professors
        with open(BASE_DIR / "professors_loaded.pkl", "rb") as f:
            self.professors = pickle.load(f)

        # Load BM25 index
        with open(BASE_DIR / "bm25_index.pkl", "rb") as f:
            data = pickle.load(f)
            self.bm25 = data["bm25"]
            self.tokenized_corpus = data["corpus"]

        # Load TF-IDF index
        with open(BASE_DIR / "tfidf_index.pkl", "rb") as f:
            data = pickle.load(f)
            self.tfidf_vectorizer = data["vectorizer"]
            self.tfidf_matrix = data["matrix"]

        # Load name variants
        with open(BASE_DIR / "name_variants.json", "r", encoding="utf-8") as f:
            self.name_variants = json.load(f)

        self._loaded = True

    def _name_match_scores(self, query: str) -> np.ndarray:
        """
        Step 1: Name matching — fastest check.
        Returns array of scores [0..1] for each professor.
        """
        scores = np.zeros(len(self.professors))
        query_lower = query.lower().strip()
        query_clean = re.sub(r"\.", " ", query_lower)
        query_clean = re.sub(r"\s+", " ", query_clean).strip()

        # Check name variants index for exact variant match
        matched_indices = set()
        if query_clean in self.name_variants:
            for idx in self.name_variants[query_clean]:
                scores[idx] = max(scores[idx], 0.9)
                matched_indices.add(idx)

        # Check all single-word variants (last name match)
        query_tokens = query_clean.split()
        for token in query_tokens:
            if token in self.name_variants:
                for idx in self.name_variants[token]:
                    scores[idx] = max(scores[idx], 0.8)
                    matched_indices.add(idx)

        # Per-professor name matching
        for idx, prof in enumerate(self.professors):
            prof_name = prof.get("name", "").lower().strip()
            prof_clean = re.sub(r"\.", " ", prof_name)
            prof_clean = re.sub(r"\s+", " ", prof_clean).strip()

            # Exact match
            if query_clean == prof_clean:
                scores[idx] = 1.0
                continue

            # Query is substring of professor name
            if query_clean in prof_clean:
                scores[idx] = max(scores[idx], 0.85)
                continue

            # Professor name contains query as a word
            prof_tokens = prof_clean.split()
            if any(query_clean == t for t in prof_tokens):
                scores[idx] = max(scores[idx], 0.8)
                continue

            # Initials match: "R.I. Sujith" style
            if len(query_tokens) >= 2:
                # Check if last token matches last name
                if query_tokens[-1] == prof_tokens[-1]:
                    scores[idx] = max(scores[idx], 0.7)
                    continue

            # Fuzzy match using difflib
            if idx not in matched_indices:
                ratio = difflib.SequenceMatcher(None, query_clean, prof_clean).ratio()
                if ratio > 0.6:
                    scores[idx] = max(scores[idx], ratio * 0.7)

        return scores

    def _bm25_scores(self, query: str) -> np.ndarray:
        """Step 2: BM25 keyword search. Returns normalized scores."""
        tokenized_query = query.lower().split()
        raw_scores = self.bm25.get_scores(tokenized_query)
        max_score = raw_scores.max()
        if max_score > 0:
            return raw_scores / max_score
        return raw_scores

    def _tfidf_scores(self, query: str) -> np.ndarray:
        """Step 3: TF-IDF semantic search. Returns cosine similarity scores."""
        query_vec = self.tfidf_vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix).flatten()
        return similarities

    def search(
        self,
        query: str,
        dept_filter: Optional[str] = None,
        top_k: int = 5,
    ) -> dict:
        """
        Hybrid search combining name matching + BM25 + TF-IDF.

        Returns:
            dict with keys: results, disambiguation, search_time_ms, query
        """
        self.load_indexes()
        start = time.perf_counter()

        # Step 1-3: Get individual scores
        name_scores = self._name_match_scores(query)
        bm25_scores = self._bm25_scores(query)
        tfidf_scores = self._tfidf_scores(query)

        # Step 4: Combine scores
        # final_score = (name_score * 0.5) + (bm25_score * 0.3) + (tfidf_score * 0.2)
        final_scores = (name_scores * 0.5) + (bm25_scores * 0.3) + (tfidf_scores * 0.2)

        # Step 5: Apply department filter boost
        if dept_filter:
            dept_lower = dept_filter.lower()
            for idx, prof in enumerate(self.professors):
                prof_dept = prof.get("department", "").lower()
                if dept_lower in prof_dept or prof_dept in dept_lower:
                    final_scores[idx] += 0.2

        # Get top-k indices
        top_indices = np.argsort(final_scores)[::-1][:top_k]

        # Build results
        results = []
        for idx in top_indices:
            if final_scores[idx] < 0.01:
                continue
            prof = self.professors[idx]
            ri = prof.get("Research Interests", [])
            if isinstance(ri, str):
                ri = [ri]

            # Determine match type
            ns = name_scores[idx]
            if ns >= 0.9:
                match_type = "exact_name"
            elif ns >= 0.7:
                match_type = "partial_name"
            elif bm25_scores[idx] > tfidf_scores[idx]:
                match_type = "keyword"
            else:
                match_type = "semantic"

            results.append(SearchResult(
                index=int(idx),
                name=prof.get("name", ""),
                department=prof.get("department", ""),
                designation=prof.get("designation", ""),
                confidence=float(final_scores[idx]),
                name_score=float(name_scores[idx]),
                bm25_score=float(bm25_scores[idx]),
                tfidf_score=float(tfidf_scores[idx]),
                match_type=match_type,
                research_areas=ri[:5],
            ))

        elapsed_ms = (time.perf_counter() - start) * 1000

        # Step 6: Entity resolution / disambiguation
        disambiguation = None
        if len(results) >= 2:
            score_gap = results[0].confidence - results[1].confidence
            if score_gap < 0.1:
                # Multiple close matches — disambiguation needed
                close_matches = [r for r in results if results[0].confidence - r.confidence < 0.1]
                disambiguation = (
                    f"Found {len(close_matches)} professors matching '{query}' — "
                    f"please specify department or full name to narrow down."
                )

        return {
            "query": query,
            "results": [r.to_dict() for r in results],
            "disambiguation": disambiguation,
            "search_time_ms": round(elapsed_ms, 2),
            "total_indexed": len(self.professors),
        }

    def get_professor(self, index: int) -> dict:
        """Get a professor by index."""
        self.load_indexes()
        if 0 <= index < len(self.professors):
            return self.professors[index]
        return {}

    def get_department_professors(
        self,
        department: str,
        sort_by: str = "name",
        seniority: str = "all",
    ) -> list:
        """Get all professors in a department with sorting and filtering."""
        self.load_indexes()
        dept_lower = department.lower()
        results = []
        for idx, prof in enumerate(self.professors):
            if dept_lower in prof.get("department", "").lower():
                if seniority != "all":
                    desig = prof.get("designation", "").lower()
                    if seniority.lower() not in desig:
                        continue
                results.append({
                    "index": idx,
                    "name": prof.get("name", ""),
                    "department": prof.get("department", ""),
                    "designation": prof.get("designation", ""),
                    "research_areas": prof.get("Research Interests", []),
                    "expertise": prof.get("Areas of expertise", []),
                })

        # Sorting
        if sort_by == "name":
            results.sort(key=lambda x: x["name"])
        elif sort_by == "designation":
            rank = {"professor": 0, "associate professor": 1, "assistant professor": 2}
            results.sort(key=lambda x: rank.get(x["designation"].lower(), 3))

        return results

    def get_all_departments(self) -> list:
        """Get list of all departments."""
        self.load_indexes()
        depts = sorted(set(p.get("department", "") for p in self.professors))
        return [d for d in depts if d]

    def autocomplete(self, prefix: str, limit: int = 10) -> list:
        """Return name suggestions for autocomplete."""
        self.load_indexes()
        prefix_lower = prefix.lower().strip()
        if len(prefix_lower) < 2:
            return []
        suggestions = []
        seen = set()
        for prof in self.professors:
            name = prof.get("name", "")
            name_lower = name.lower()
            if prefix_lower in name_lower and name not in seen:
                suggestions.append({
                    "name": name,
                    "department": prof.get("department", ""),
                    "designation": prof.get("designation", ""),
                })
                seen.add(name)
                if len(suggestions) >= limit:
                    break
        return suggestions
