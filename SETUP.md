# CollabV AI -- Setup Guide

## What is CollabV AI

B2B platform that matches companies with IIT Madras professors for R&D collaboration.

- **543 professors** across **16 departments**
- AI-powered 3-tier matching engine (keyword + TF-IDF + soft filters)
- NLP-based need parsing (free text to structured tags)
- REST API + web UI
- SQLite persistence for match history

---

## Folder Structure

```
collabv_scraper/
|-- collabv/
|   |-- api.py              # FastAPI REST server
|   |-- matching_engine.py  # Model 1 - 3-tier scoring engine
|   |-- need_parser.py      # Model 5 - Text to structured tags
|   |-- profile_nlp.py      # Model 2 - Professor NLP enrichment
|   |-- database.py         # SQLite persistence layer
|   |-- demo.py             # Batch demo on 100 companies
|   |-- frontend.html       # Web UI (dark theme)
|   +-- __init__.py
|-- iitm_professors_nlp.json          # 543 enriched professor profiles
|-- 100_Companies_Collaboration_Schema.xlsx  # 100 company test data
|-- update_professors.py    # Refresh publications from OpenAlex API
|-- requirements.txt        # Python dependencies
|-- .env.example            # Config template
|-- run.bat                 # Windows startup script
|-- run.sh                  # Linux/Mac startup script
+-- SETUP.md                # This file
```

---

## Quick Start (3 commands)

```bash
pip install -r requirements.txt
copy .env.example .env          # Windows
# cp .env.example .env          # Linux/Mac
uvicorn collabv.api:app --port 8000 --reload
```

Then open **http://localhost:8000** in your browser.

Or use the startup scripts:

```bash
# Windows
run.bat

# Linux/Mac
chmod +x run.sh && ./run.sh
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | System health check + stats |
| GET | `/` | Web UI (frontend.html) |
| GET | `/professors?limit=10` | List professors (optional dept filter) |
| POST | `/needs/parse` | Parse raw company text into structured tags |
| POST | `/company/request` | Submit a structured company request |
| POST | `/match/run` | Run matching (accepts `raw_text` or `company_id`) |
| GET | `/match/results/{id}` | Get cached match results |
| POST | `/feedback/submit` | Log accept/reject feedback |
| GET | `/history` | Last 20 matches from database |
| GET | `/docs` | Auto-generated API docs (Swagger UI) |

### Quick API Test

```bash
# Raw text matching (one call)
curl -X POST http://localhost:8000/match/run \
  -H "Content-Type: application/json" \
  -d '{"raw_text":"We need ML expertise for autonomous vehicles", "company_name":"TestCo", "top_k":5}'
```

---

## How to Run the Demo (100 companies)

```bash
python collabv/demo.py
```

This loads all 100 companies from the Excel file, runs matching for each, and saves results to `collabv_results_100.xlsx`.

---

## How to Update Professor Data

```bash
pip install aiohttp beautifulsoup4 lxml tqdm
python update_professors.py --workers 3 --delay 1.5
```

This refreshes publications from the OpenAlex API (free, no key needed). Takes about 20 minutes for all 543 professors. After updating, re-run NLP enrichment:

```bash
python -m collabv.profile_nlp
```

---

## Performance

| Metric | Value |
|--------|-------|
| Professors indexed | 543 |
| Departments covered | 14 out of 16 |
| Average match score | 76.7 / 100 |
| Speed per company | 0.24 seconds |
| Top-1 scores above 70 | 93% |
| Real publication data | 96% of professors |

---

## How Matching Works

### Tier 1: Keyword + Domain Alignment (55%)
- Technical area vs research interests (30%)
- Domain-to-department mapping (25%)
- 200+ industry-to-department routing rules
- NLP industry fit bonus from professor profiles

### Tier 2: Semantic Similarity (45%)
- TF-IDF vectorization with bigrams
- Cosine similarity with rank normalization
- Score amplification for better separation
- Required expertise (30%), project description (10%), challenges (5%)

### Tier 3: Soft Filters (5%)
- Location preference (2%)
- Collaboration type fit (2%)
- Research level / seniority match (1%)

### Post-processing
- Diversity boost: penalizes top-3 same-department monopoly
- Results sorted by final weighted score (0-100 scale)

---

## Models Built

| Model | File | Description |
|-------|------|-------------|
| Model 1 | `matching_engine.py` | 3-tier professor-company scoring |
| Model 2 | `profile_nlp.py` | Professor expertise extraction (50 domains, 18 industries) |
| Model 5 | `need_parser.py` | Company text to structured tags (Claude API + rule-based) |

## Models Planned (Phase 2)

| Model | Description |
|-------|-------------|
| Model 3 | Patent Valuation - Score professors by patent portfolio |
| Model 4 | Collaboration Readiness - Predict professor availability |
| Model 6 | Deal Scoring - Estimate collaboration success probability |
| Model 7 | Contract NLP - Extract key terms from MoU documents |

---

## Environment Variables (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `COLLABV_API_KEY` | changeme | API authentication key |
| `ANTHROPIC_API_KEY` | (none) | For Claude-based need parsing |
| `PORT` | 8000 | Server port |
| `PROFESSORS_FILE` | iitm_professors_nlp.json | Professor data file |
| `DB_FILE` | collabv_data.db | SQLite database file |
| `DEBUG` | false | Enable debug mode |

---

## Tech Stack

- **Backend**: Python 3.10+, FastAPI, Uvicorn
- **ML**: scikit-learn (TF-IDF), scipy (rank normalization), numpy
- **NLP**: Rule-based keyword extraction (200+ patterns)
- **Data**: pandas, openpyxl for Excel I/O
- **Database**: SQLite3 (built-in)
- **Frontend**: Vanilla HTML/CSS/JS (no frameworks)
- **Scraping**: aiohttp, BeautifulSoup, OpenAlex API, Semantic Scholar API
