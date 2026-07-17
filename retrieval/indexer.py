"""
Offline pipeline — runs once to build search indexes.
Builds: BM25 index, TF-IDF semantic index, Name variants index.
"""

import json
import os
import pickle
import re
import sys
from pathlib import Path

from rank_bm25 import BM25Okapi
from sklearn.feature_extraction.text import TfidfVectorizer

# Paths
BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / "data" / "iitm_professors_final_corrected.json"
BM25_INDEX_PATH = BASE_DIR / "bm25_index.pkl"
TFIDF_INDEX_PATH = BASE_DIR / "tfidf_index.pkl"
NAME_VARIANTS_PATH = BASE_DIR / "name_variants.json"
PROFESSORS_PATH = BASE_DIR / "professors_loaded.pkl"


def load_professors():
    """Load all professors from the JSON data file."""
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        professors = json.load(f)
    print(f"Loaded {len(professors)} professors from data file")
    return professors


def build_bm25_corpus(professor):
    """Build a searchable text string for one professor."""
    parts = [
        professor.get("name", ""),
        professor.get("department", ""),
        professor.get("designation", ""),
    ]
    # Research interests
    ri = professor.get("Research Interests", "")
    if isinstance(ri, list):
        parts.extend(ri)
    elif isinstance(ri, str):
        parts.append(ri)
    # Areas of expertise
    exp = professor.get("Areas of expertise", [])
    if isinstance(exp, list):
        parts.extend(exp)
    elif isinstance(exp, str):
        parts.append(exp)
    # Courses
    courses = professor.get("Courses Taught", "")
    if isinstance(courses, list):
        parts.extend(courses)
    elif isinstance(courses, str):
        parts.append(courses)
    return " ".join(str(p) for p in parts if p)


def build_full_text(professor):
    """Build a full text profile for TF-IDF vectorization."""
    parts = [
        professor.get("name", ""),
        professor.get("department", ""),
        professor.get("designation", ""),
    ]
    ri = professor.get("Research Interests", "")
    if isinstance(ri, list):
        parts.extend(ri)
    elif isinstance(ri, str):
        parts.append(ri)
    exp = professor.get("Areas of expertise", [])
    if isinstance(exp, list):
        parts.extend(exp)
    elif isinstance(exp, str):
        parts.append(exp)
    # Publications
    pubs = professor.get("Most recently published papers or publications", [])
    if isinstance(pubs, list):
        parts.extend(pubs)
    elif isinstance(pubs, str):
        parts.append(pubs)
    courses = professor.get("Courses Taught", "")
    if isinstance(courses, list):
        parts.extend(courses)
    elif isinstance(courses, str):
        parts.append(courses)
    # Education
    edu = professor.get("Education", [])
    if isinstance(edu, list):
        parts.extend(edu)
    elif isinstance(edu, str):
        parts.append(edu)
    return " ".join(str(p) for p in parts if p)


def generate_name_variants(name: str) -> list:
    """
    Generate multiple search-friendly variants of a professor name.
    E.g. "R.I. Sujith" → ["sujith", "r i sujith", "ri sujith", "r.i. sujith"]
    E.g. "Hema A Murthy" → ["hema murthy", "hema a murthy", "h a murthy", "murthy"]
    """
    if not name:
        return []

    original_lower = name.lower().strip()
    variants = set()
    variants.add(original_lower)

    # Remove dots and normalize
    cleaned = re.sub(r"\.", " ", original_lower)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    variants.add(cleaned)

    # Split into tokens
    tokens = cleaned.split()
    if not tokens:
        return list(variants)

    # Last name (assume last token)
    last_name = tokens[-1]
    variants.add(last_name)

    # First + last (skip middle initials)
    if len(tokens) >= 2:
        # Check which tokens are initials (1-2 chars)
        non_initials = [t for t in tokens if len(t) > 2]
        initials = [t for t in tokens if len(t) <= 2]

        if len(non_initials) >= 2:
            # e.g. "Mitesh Khapra" or "Hema Murthy"
            variants.add(f"{non_initials[0]} {non_initials[-1]}")

        # All initials collapsed + last name: "R I Sujith" → "ri sujith"
        if initials and non_initials:
            collapsed = "".join(initials)
            variants.add(f"{collapsed} {non_initials[-1]}")

        # Version without dots but with spaces between initials
        # "r i sujith" already handled by cleaned

        # First name + last name
        variants.add(f"{tokens[0]} {tokens[-1]}")

        # Initials with dots: "r.i. sujith"
        if initials:
            dotted = ".".join(initials) + "."
            variants.add(f"{dotted} {last_name}")

    # Remove empty strings
    variants.discard("")
    return list(variants)


def build_bm25_index(professors):
    """Build and save BM25 index."""
    print("Building BM25 index...")
    corpus = [build_bm25_corpus(p) for p in professors]
    tokenized_corpus = [doc.lower().split() for doc in corpus]
    bm25 = BM25Okapi(tokenized_corpus)
    with open(BM25_INDEX_PATH, "wb") as f:
        pickle.dump({"bm25": bm25, "corpus": tokenized_corpus}, f)
    print(f"  BM25 index saved to {BM25_INDEX_PATH}")
    return bm25


def build_tfidf_index(professors):
    """Build and save TF-IDF index."""
    print("Building TF-IDF semantic index...")
    full_texts = [build_full_text(p) for p in professors]
    vectorizer = TfidfVectorizer(
        max_features=10000,
        stop_words="english",
        ngram_range=(1, 2),
        sublinear_tf=True,
    )
    tfidf_matrix = vectorizer.fit_transform(full_texts)
    with open(TFIDF_INDEX_PATH, "wb") as f:
        pickle.dump({"vectorizer": vectorizer, "matrix": tfidf_matrix}, f)
    print(f"  TF-IDF index saved to {TFIDF_INDEX_PATH}")
    return vectorizer, tfidf_matrix


def build_name_variants_index(professors):
    """Build and save name variants index."""
    print("Building name variants index...")
    variants_index = {}
    for idx, prof in enumerate(professors):
        name = prof.get("name", "")
        variants = generate_name_variants(name)
        for v in variants:
            if v not in variants_index:
                variants_index[v] = []
            variants_index[v].append(idx)
    with open(NAME_VARIANTS_PATH, "w", encoding="utf-8") as f:
        json.dump(variants_index, f, indent=2)
    print(f"  Name variants index saved to {NAME_VARIANTS_PATH}")
    print(f"  Total unique name variants: {len(variants_index)}")
    return variants_index


def main():
    print("=" * 60)
    print("Faculty Information Retrieval — Index Builder")
    print("=" * 60)

    # Load data
    professors = load_professors()

    # Save professors for quick loading
    with open(PROFESSORS_PATH, "wb") as f:
        pickle.dump(professors, f)

    # Build all indexes
    build_bm25_index(professors)
    build_tfidf_index(professors)
    build_name_variants_index(professors)

    print()
    print(f"Index built: {len(professors)} professors, BM25 ready, TF-IDF ready")
    print("=" * 60)


if __name__ == "__main__":
    main()
