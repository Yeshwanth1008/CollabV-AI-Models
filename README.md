# CollabV AI

**Match your innovation with India's top academic minds.**

CollabV AI is a B2B platform that ranks IIT Madras professors against any
company R&D brief in under a second, surfacing not just *who* to talk to but
*how likely the deal is to close*, *what the risks are*, and *what to bring
to the first meeting*.

543 professors. 16 departments. 1,391 patent records. 8 scoring layers.

---

## Why CollabV exists

University-industry collaboration in India is a discovery problem masquerading
as a relationship problem. Most companies that want academic R&D either
(a) only know the famous names, or (b) waste weeks cold-emailing through
generic Tech Transfer offices.

CollabV replaces that with a software-driven match: paste your R&D brief, get
ranked professors with confidence scores, deal probabilities, and pre-drafted
MoUs.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          Frontend (Next.js 14)                       │
│   App Router · React Query · Tailwind · Recharts · TypeScript        │
└────────────────────────────────┬────────────────────────────────────┘
                                 │  HTTPS
┌────────────────────────────────▼────────────────────────────────────┐
│                       FastAPI Backend (Python 3.11)                  │
│                                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │   Auth +     │  │  Rate Limit  │  │ CORS / HSTS  │  │  Request │ │
│  │   API Keys   │  │  + Quota     │  │  Security    │  │    ID    │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────┘ │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │                    Matching Engine v3                            │ │
│  │                                                                  │ │
│  │   Tier 1 (45%)  · Keyword + domain-department alignment          │ │
│  │   Tier 2 (30%)  · Dense embeddings (sentence-transformers/FAISS) │ │
│  │   Tier 3 (5%)   · Soft filters: location, collab type            │ │
│  │   + Patent score (10%)   · Model 3: portfolio + relevance        │ │
│  │   + Readiness   (10%)    · Model 4: industry / pubs / patents    │ │
│  └─────────────────────────────────────────────────────────────────┘ │
│                                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
│  │  Deal Score  │  │   Contract   │  │  Explainer   │  │ Retrain  │ │
│  │  (Model 6)   │  │  NLP (M7)    │  │   (Claude)   │  │  (M8)    │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────┘ │
└────────────────────────────────┬────────────────────────────────────┘
                                 │
       ┌─────────────────────────┼─────────────────────────┐
       │                         │                         │
┌──────▼─────┐         ┌─────────▼────────┐       ┌────────▼──────┐
│ PostgreSQL │         │      Redis       │       │  Claude API   │
│ + pgvector │         │ (cache, queue)   │       │  (Anthropic)  │
└────────────┘         └──────────────────┘       └───────────────┘
```

---

## The 8 layers

| # | Layer | What it does | File |
|---|-------|--------------|------|
| 1 | **3-tier matching engine** | Keyword + dense embeddings + soft filters, weighted by a trained vector | `collabv/matching_engine.py` |
| 2 | **Professor NLP enrichment** | Tags each professor's biography across 50 research domains | `collabv/profile_nlp.py` |
| 3 | **Patent valuation** | Scores each professor's portfolio (count, recency, status, diversity, collaboration) and its relevance to the request | `collabv/patent_scorer.py` |
| 4 | **Collaboration readiness** | Industry engagement + publication velocity + patent activity + bandwidth + dept infrastructure | `collabv/collab_readiness.py` |
| 5 | **Need parser** | Extracts structured fields from plain-text company briefs (Claude API + rule-based fallback) | `collabv/need_parser.py` |
| 6 | **Deal success scoring** | Logistic combination of match quality, breadth, history, complexity fit, feasibility | `collabv/deal_scorer.py` |
| 7 | **Contract / MoU NLP** | Generates contracts from 5 templates, parses uploaded contracts, diffs two contracts | `collabv/contract_nlp.py` |
| 8 | **Feedback retraining** | Nelder-Mead weight optimization from accept/reject feedback | `collabv/retrainer.py` |

---

## Benchmarks

Measured on 100 real company briefs from a private dataset (Intel i7-1165G7, no GPU):

| Metric | Value |
|---|---|
| Match latency (p50) | **0.77s per company** |
| Match latency (full v3 stack with deal scores + LLM explanations) | **~1.4s** |
| Top-1 average score | **63.0 / 100** |
| Top-1 average deal probability | **70.4 %** |
| Score distribution (top-1) | 5% in 70-79, 80% in 60-69, 15% in 50-59 |
| Department coverage | All 16 IITM departments returned at least one top-5 match |
| Embedding index size | ~8 MB (FAISS flat-IP, 384-dim MiniLM) |

Once retrained on 150 real accept/reject feedback records, the engine reshuffled
top-5 rankings meaningfully — e.g. the #1 dropped out of top-5 on subsequent
queries and a previously-out-of-top-5 professor moved to #1, with a +0.19
improvement in the accept-vs-reject score gap.

---

## API at a glance

All endpoints are documented at `/docs` (Swagger UI). The five you'll use most:

### 1. Submit a brief and get matches

```bash
curl -X POST https://app.collabv.ai/match/run \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "raw_text": "We need ML expertise for autonomous vehicle perception in Indian traffic. 18-month JRA, shared IP.",
    "company_name": "AutonomyLabs",
    "top_k": 5,
    "include_deal_score": true,
    "include_explanations": true
  }'
```

Returns ranked professors with `score`, `patent_score`, `readiness_score`,
`deal_probability`, and an LLM-generated `explanation` with strengths/gaps/
talking points.

### 2. Inspect a professor's patent portfolio

```bash
curl https://app.collabv.ai/professor/{professor_id}/patents \
  -H "X-API-Key: $API_KEY"
```

### 3. Generate a Joint Research Agreement

```bash
curl -X POST https://app.collabv.ai/contract/generate \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "type": "joint_research",
    "company_name": "AutonomyLabs",
    "professor_name": "Dr. Anurag Mittal",
    "department": "Computer Science & Engineering",
    "research_area": "Computer Vision",
    "amount": 2500000
  }'
```

### 4. Submit feedback (feeds retraining)

```bash
curl -X POST https://app.collabv.ai/feedback/submit \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"match_id":"M-...","professor_id":"...","action":"accept"}'
```

### 5. Department readiness heatmap

```bash
curl https://app.collabv.ai/readiness/departments \
  -H "X-API-Key: $API_KEY"
```

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | FastAPI + Python 3.11 | Async, type-safe, automatic OpenAPI docs |
| Database | PostgreSQL 16 + pgvector | Native vector search; no separate vector DB to operate |
| Vector index | FAISS (dev) / pgvector (prod) | Sub-10ms semantic search across 543 professors |
| LLM | Claude Sonnet 4.6 | Explanations + contract enrichment; graceful rule-based fallback |
| Embeddings | sentence-transformers MiniLM-L6 | 384-dim, fast, no GPU needed |
| Cache | Redis | Rate limiting + LLM response cache |
| Auth | bcrypt + PyJWT + API keys | Per-tier quotas (free / pro / enterprise) |
| Frontend | Next.js 14 + Tailwind + React Query | Production-grade B2B UX |
| Infra | AWS ECS Fargate + RDS Multi-AZ + ALB | Auto-scaling, blue/green deploys |
| IaC | Terraform | Reproducible cloud setup |
| CI/CD | GitHub Actions | Test on PR, deploy on merge to main |
| Monitoring | Sentry + CloudWatch + structured JSON logs | Errors, latency, request tracing |

---

## Quick start

```bash
# Backend (with synthetic patent data already loaded)
pip install -r requirements.txt
uvicorn collabv.api:app --reload

# Frontend
cd frontend
cp .env.local.example .env.local
npm install
npm run dev    # http://localhost:3000

# Run the 100-company demo
python collabv/demo.py    # -> collabv_v3_results_100.xlsx

# Investor walkthrough
python scripts/demo-walkthrough.py
```

To deploy to AWS:

```bash
./scripts/setup-aws.sh         # One-time: ECR repos, S3 state, secrets
./scripts/deploy.sh             # Build + push + terraform apply + migrate
./scripts/smoke-test-production.sh
```

See [infrastructure/README.md](infrastructure/README.md) for cost estimates
(~$200/month) and AWS setup details.

---

## AI search & retrieval platform

Two additional backend-only FastAPI services, meant to run alongside
`collabv/api.py` and be called from this same frontend — neither serves a
UI of its own:

- **`retrieval/`** (port 8001) — single-professor lookup with live
  enrichment (citations, h-index, co-authors) from OpenAlex, Semantic
  Scholar, ORCID, CrossRef, and arXiv. See
  [retrieval/SETUP.md](retrieval/SETUP.md).
- **`search_platform/`** (port 8002) — hybrid RAG semantic search and
  recommendations across every CollabV role (students, professors,
  researchers, employees, companies, startups, institutes, alumni,
  mentors), reading live from this repo's own data — no seed/mock data,
  consistent with the live-data-only policy above. See
  [search_platform/ARCHITECTURE.md](search_platform/ARCHITECTURE.md),
  particularly §10 for integration details (base URLs, CORS, auth status).

```bash
pip install -r retrieval/requirements.txt -r search_platform/requirements.txt
python retrieval/indexer.py && uvicorn retrieval.api:app --port 8001 --reload
python -m search_platform.sync_from_collabv && uvicorn search_platform.api:app --port 8002 --reload
```

Both are early-stage: no authentication on any endpoint yet (see
`search_platform/ARCHITECTURE.md`'s "What's deferred"), not yet
containerized, and search_platform's Postgres vector search currently runs
on an in-process NumPy fallback rather than pgvector (see
`search_platform/INSTALL_PGVECTOR.md`).

---

## What's still synthetic, what's real

- **Real**: 543 IIT Madras professor profiles (names, departments, biographies,
  research areas, publications, NLP enrichment)
- **Synthetic** (drop-in replaceable): patent data. The scraper supports a
  configurable IITM API endpoint via `COLLABV_IITM_PATENT_API` and the
  `--use-synthetic` flag is a placeholder until the real endpoint is wired.
- **Real**: every scoring algorithm operates on the same shape of data as
  production — switching to real patent data only changes the scores, not the
  code.

---

## License

Proprietary. © 2026 CollabV AI Pvt. Ltd. All rights reserved.

For commercial licensing inquiries, contact us.
