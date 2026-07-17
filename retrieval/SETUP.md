# Faculty Information Retrieval System

## What it does
Given a professor name, retrieves complete structured profile
from 6 data sources in < 2 seconds.

## Quick Start
```bash
pip install -r requirements.txt
python retrieval/indexer.py
uvicorn retrieval.api:app --port 8001 --reload
```
Backend-only service, no UI — explore it at http://localhost:8001/docs
(Swagger). Meant to be called by the CollabV website's own frontend.

## Search Examples
- Exact: "Mitesh Khapra"
- Partial: "Sujith" → finds R.I. Sujith
- Ambiguous: "Krishna" → shows 5 matches with confidence scores

## API Endpoints
```
GET /search?q=NAME&dept=DEPT     - Fast search (< 50ms)
GET /profile?name=NAME           - Full enriched profile
POST /compare                    - Side-by-side comparison
GET /department?name=DEPT        - Browse by department
GET /stats                       - System statistics
GET /health                      - Health check
```

## Data Sources (6 Total)

| Source | Type | What it provides |
|--------|------|-----------------|
| Local DB | Offline | Dept, email, research areas, NLP tags |
| OpenAlex | API | Publications, citations, H-index |
| Semantic Scholar | API | Backup citations, co-authors |
| ORCID | API | Verified identity, education, employment |
| CrossRef | API | DOI, journal metadata, citation counts |
| arXiv | API | Preprints for CS/Physics/Math professors |

## Source Coverage (sampled 20 professors)

| Source | Coverage |
|--------|----------|
| Local DB | 100% |
| OpenAlex | 90% |
| ORCID | 80% |
| CrossRef | 100% |
| arXiv | 80% |
| Semantic Scholar | 60% |
| **Avg sources/prof** | **5.1 / 6** |

## Confidence Scoring
- Local DB match: base 0.30
- + OpenAlex found: +0.40
- + Semantic Scholar: +0.30
- + ORCID verified: +0.05
- + CrossRef papers: +0.05
- + arXiv preprints: +0.05
- Max confidence: 1.0

## Performance
- 543 professors indexed
- Avg search: 23ms
- Avg profile build: 1134ms (all 6 sources in parallel)
- Cache TTL: 7 days

## Files
```
retrieval/indexer.py       - Build search indexes (run once)
retrieval/search_engine.py - Hybrid BM25 + TF-IDF search
retrieval/profile_builder.py - 6-source profile aggregation
retrieval/cache.py         - SQLite cache
retrieval/api.py           - FastAPI server (backend-only, no UI)
retrieval/demo.py          - Test script + source coverage scan
```
