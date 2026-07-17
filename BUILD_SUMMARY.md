# CollabV AI v3.0 - Build Summary

End-to-end build of all 7 ML/NLP models + production deployment scaffolding,
following the completion guide.

## Marketplace status

The Tech-Transfer marketplace is built end-to-end and browser-verified. The
inventor activation flow, claim approval, browse, public detail, inquiry round-
trip, Mode B recommendations, and inquiry inbox all work in real UI against a
freshly-seeded DB. See the `marketplace-smoke` CI job — it gates PRs on 43
assertions covering engine readiness, lifecycle security, claim guard, and
buyer flow.

### Resolved limitations (no longer applicable)

| Was | Resolution |
|---|---|
| `/marketplace/inventor/claim` impersonation hole | **Fixed.** Claim now creates a pending request; admin must explicitly approve before `linked_professor_id` is set. Email-domain / SSO automation noted as scale-fix TODO in the table comment. |
| Mode B verified-as-code, dormant-in-practice | **Live.** Patent index persists to disk (`marketplace_patents.index`), incremental upsert+save on activation, indexer drops listings on pause/withdraw. Verified live: chemistry/water buyer surfaces topical patents at composite score 66 with domain=100 + industry=100. |
| Inventor-paste abstracts not wired | **Live.** Abstract textarea is editable in draft/paused; PATCH flips `abstract_status` to `'pasted'` automatically. Non-blocking nudge encourages it before Submit. Cosine measurement showed +0.34 toward topical siblings (clean signal) when abstract is added. |
| Buyer population AI/ML/IoT-skewed (couldn't match hardware patents) | **Mitigated.** Added 35 domain-matched synthetic buyers covering materials, chemicals, biotech, energy, fluid_thermal, optics, healthcare, civil, manufacturing. Real buyer signups still the proper Phase-2 fix. |
| Silent engine-unavailable failure mode | **Fixed.** Embedding model load failures now log `[EMBEDDINGS DEGRADED]` at error level, expose a `load_error` reason on `/marketplace/status` + `marketplace_embeddings_degraded` on `/health`, and Mode A/B return `status: "engine_unavailable"` (not empty candidates). Smoke CI asserts `degraded=false` so a broken environment can't masquerade as "no matches." |

### Carried limitations

| Area | Limitation | Future fix |
|---|---|---|
| `marketplace_reranker.industry_match` | Reuses `_DOMAIN_KEYWORDS` (research-domain taxonomy); some real buyer industry strings don't classify. Scores 0 for those buyers — doesn't mislead, just contributes nothing. | When real buyers onboard, add `_INDUSTRY_KEYWORDS` parallel taxonomy or a picklist on the buyer form. |
| `_async_fetch_abstract_into_db` no-op | Both upstream sources (IITM TTO, Google Patents) failed end-to-end; flow now relies on inventor-paste. | Commercial patent API when budget allows, OR Playwright-rendered TTO scraper. Inventor-paste is the load-bearing path. |
| Indian patent number backfill (5.3% via anonymous `urllib`) | Same JS-shell-page root cause as abstracts. The 33 extracted are persisted; the other ~865 blank. Not load-bearing. | Inventor-paste at activation time, Playwright scraper, or commercial source. |
| Buyer population still 100% synthetic | 135 synthetic buyers (100 original + 35 domain-matched). All carry `is_synthetic=True` and are excluded from real inventor rankings by default. | Real buyer signups. Don't tune the reranker against synthetic distribution. |
| Stub merge: `STUB-BASAVARAJA` (1 listing) | Held in earlier round for manual review — similarity-to-faculty was sub-0.95. | 30-second eyeball next time you're in the IITM directory; run `scripts/merge_stub_duplicates.py` if it matches a real faculty record. |

## What was built

### Backend models (collabv/)

| File | Lines | Purpose |
|---|---|---|
| `patent_scorer.py` | ~330 | Model 3 - Patent portfolio + relevance scoring with configurable weights |
| `collab_readiness.py` | ~350 | Model 4 - 5-signal readiness predictor + contextual scoring + dept aggregation |
| `deal_scorer.py` | ~370 | Model 6 - Deal success probability via logistic combination + risk factors |
| `contract_nlp.py` | ~570 | Model 7 - Contract parsing (regex + Claude), diff, and 5 embedded templates |
| `embeddings.py` | ~230 | Dense embeddings via sentence-transformers + FAISS (numpy fallback) |
| `explainer.py` | ~280 | LLM match explanations (Claude + rule-based fallback + SQLite cache) |
| `retrainer.py` | ~340 | Feedback retraining via Nelder-Mead with movement caps + weight history |
| `patent_scraper.py` | ~320 | Patent scraper (IITM API → Google Patents → synthetic fallback) |
| `auth.py` | ~250 | JWT auth + bcrypt + role-based access + per-tier rate limiting |
| `monitoring.py` | ~150 | Structured JSON logs + Sentry + detailed health endpoint |

### Backend wiring updates

- `matching_engine.py`: loads patent + readiness scorers, uses dense embeddings
  for Tier 2 with TF-IDF fallback, applies trained weights from `collabv_weights.json`
- `database.py`: added `deal_assessments`, `match_explanations`, `weight_history` tables
- `api.py`: ~20 new endpoints for patents, readiness, deal scoring, contracts,
  retraining, explanations, embeddings rebuild

### Frontend (frontend/)

Production Next.js 14 app with App Router, TypeScript, Tailwind, shadcn-style
components, React Query for data fetching.

| Route | Purpose |
|---|---|
| `/` | Landing page |
| `/login` | Auth + Phase-1 testing note (claim-approval gate documented) |
| `/match` | Submit R&D need, view ranked matches with explanations + deal scores |
| `/professors` | Searchable directory |
| `/professors/[id]` | Full profile with patent portfolio + readiness breakdown |
| `/analytics` | Department readiness chart + retraining controls |
| `/contracts` | Generate MoU templates + parse existing contracts |
| `/marketplace/browse` | Public patent grid — active listings only, search + domain/industry filter, abstract-placeholder + IDF#/IN# fallback rendering |
| `/marketplace/patents/[id]` | Public detail page; 404s cleanly on non-active (no existence leak); auth-gated inquiry action |
| `/marketplace/inventor` | Inventor dashboard — listings grouped by status, claim-state branching (none/pending/rejected/approved) |
| `/marketplace/inventor/listings/[id]` | Edit + inventor-paste abstract + Submit-for-approval (stub-suppressed when owner is patent_stub) |
| `/marketplace/admin` | Admin queue — claim requests (Approve/Reject) + listing approvals (Approve/Send-back-to-draft) |
| `/marketplace/buyer/profile` | Create/edit buyer profile |
| `/marketplace/buyer/recommendations` | Mode B — ranked patents for the logged-in buyer, with score breakdown chips + `engine_unavailable` distinct render |
| `/marketplace/buyer/inbox` | Inquiries sent (buyer side) + received-on-my-listings (inventor side), thread + Accept/Decline/Acknowledge actions |

### Deployment artifacts

- `Dockerfile.backend` - multi-stage build with ML model pre-warmed
- `frontend/Dockerfile` - Next.js standalone output
- `docker-compose.yml` - full stack (postgres+pgvector, redis, backend, frontend, nginx)
- `nginx/nginx.conf` - reverse proxy + rate limiting + security headers
- `infrastructure/main.tf` - AWS Terraform (VPC, ECS, RDS, ElastiCache, ALB, ACM, R53, S3, ECR, IAM, CloudWatch)
- `infrastructure/README.md` - deployment guide + cost estimates
- `.github/workflows/test.yml` - CI tests (ruff, mypy, bandit, frontend build)
- `.github/workflows/deploy-production.yml` - blue/green ECS deploy with Slack notify
- `.env.production.example` - all required env vars
- `requirements-prod.txt` - sentence-transformers, FAISS, postgres async, bcrypt, JWT, Sentry

## Verified working

```
$ python -c "from collabv import api, matching_engine, patent_scorer, collab_readiness, deal_scorer, contract_nlp, embeddings, explainer, retrainer, patent_scraper, auth, monitoring; print('OK')"
OK

$ python -c "from collabv.matching_engine import MatchingEngine, CompanyRequest; e=MatchingEngine(enable_embeddings=False); print(len(e.professors))"
543

# 3-second end-to-end match with the new factors:
   37.6  Krishna S                       Electrical Engineering          patent=12 readiness=57
   37.2  Anurag Mittal                   Computer Science & Engineering  patent=12 readiness=66
   36.0  Krishna Jagannathan             Electrical Engineering          patent=12 readiness=53
```

(Patent scores are low because real patent data hasn't been scraped yet. Run
`python -m collabv.patent_scraper --use-synthetic` to populate with test
patents, then they'll go up.)

## Skipped / partial

- **Patent scraper API URL**: The ip.iitm.ac.in API endpoint isn't published, so
  the scraper accepts a `--api-url` flag, falls back to Google Patents, and
  finally falls back to synthetic patents for end-to-end testing. Provide the
  real URL via `COLLABV_IITM_PATENT_API` env var or `--api-url` when found.
- **PostgreSQL migration**: SQLite still runs; the `collabv/db_postgres.py`
  async layer isn't required for the v1 launch but can be added before
  scaling beyond ~50 concurrent users.

## Pre-launch checklist (NOT code — user actions)

These gate going live with real users + real IITM IP. Track each separately.

1. **Rotate the leaked Anthropic API key** currently in `.env` (`.env` is
   gitignored, so it never reaches version control, but the key itself has
   been flagged across multiple work sessions and is **still unrotated** per
   running record as of this note — open exposure every day it remains valid.
   Action: rotate in the Anthropic console, invalidate the old key, replace in
   `.env` / deployment secrets, set a monthly spend cap.
2. **Enable Zero Data Retention** in the Anthropic account — committed to in
   the IP-office memo (Section 8). Account toggle, not code.
3. **IITM IP/TTO consent conversation** — do not activate a real patent
   listing publicly until ownership/consent is settled. Indian IITs typically
   hold faculty patent rights institutionally — confirm against the current IP
   policy.
4. **Real-inventor login readiness** — Phase-3 admin approval claim gate IS
   in place (see `professor_claims` table + `/marketplace/admin/claim-requests`),
   so the code is ready. The remaining blocker is item 3.

## CI gate

`marketplace-smoke` job in `.github/workflows/test.yml` runs
`scripts/smoke-test-production.sh` after lint/typecheck. Asserts 43 things:
engine readiness (`degraded=false`, `load_error=null`, Mode B != engine_unavailable),
lifecycle security (the four guards), claim-approval gate, and buyer flow
(browse, profile CRUD, Mode B candidates ≥1, inquiry round-trip + thread).
Exits non-zero on any failure. Verified in both states:
- Engine healthy → 43 passed, 0 failed, exit 0
- Engine degraded (no sentence-transformers) → 35 passed, 8 failed, exit 1, all 8 failures quote the actual `load_error` message.

## Quick-start

```bash
# Backend
pip install -r requirements.txt
# (optional) pip install -r requirements-prod.txt   # for embeddings + auth
uvicorn collabv.api:app --reload

# Frontend
cd frontend
cp .env.local.example .env.local
npm install
npm run dev    # http://localhost:3000

# Or everything via docker-compose:
docker compose up --build
```
