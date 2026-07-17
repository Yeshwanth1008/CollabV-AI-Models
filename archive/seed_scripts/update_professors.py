"""
CollabV — Professor Data Enrichment Scraper v2
================================================
Uses OpenAlex API (primary) and Semantic Scholar API (fallback)
for publications. IRINS for patents. Dept defaults for industry exposure.

USAGE:
    pip install aiohttp beautifulsoup4 lxml tqdm
    python scraper_v2.py [--workers 3] [--delay 1.0]
"""

import asyncio
import json
import re
import time
import random
import logging
import argparse
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote

import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm

# ─── Args ────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(description="CollabV Professor Enrichment Scraper v2")
parser.add_argument("--workers", type=int, default=3, help="Concurrent workers (default 3)")
parser.add_argument("--delay", type=float, default=1.0, help="Min delay between requests per domain (seconds)")
parser.add_argument("--input", default="iitm_professors_labeled.json", help="Input JSON file")
parser.add_argument("--output", default="iitm_professors_enriched.json", help="Output JSON file")
parser.add_argument("--retries", type=int, default=2, help="Max retries per request (default 2)")
parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout in seconds (default 15)")
args = parser.parse_args()

# ─── Setup ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler("scraper_v2.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

CHECKPOINT = "checkpoint_v2.json"
MAX_PUBS = 8
MAX_PATENTS = 10

HEADERS = {
    "User-Agent": "CollabV-Scraper/2.0 (mailto:collabv@example.com)",
    "Accept": "application/json",
}

# Separate headers for HTML scraping (IRINS)
HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


# ─── Per-Domain Rate Limiter ─────────────────────────────────────────────────

class DomainRateLimiter:
    """Ensures minimum delay between requests to the same domain."""

    def __init__(self, default_delay: float):
        self._last_request: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self._default_delay = default_delay

    def _get_domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc

    async def wait(self, url: str):
        domain = self._get_domain(url)
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()

        async with self._locks[domain]:
            now = time.monotonic()
            last = self._last_request.get(domain, 0)
            wait_time = self._default_delay - (now - last)
            if wait_time > 0:
                jitter = random.uniform(0, self._default_delay * 0.2)
                await asyncio.sleep(wait_time + jitter)
            self._last_request[domain] = time.monotonic()


# Semantic Scholar needs a longer delay to avoid 429s
rate_limiter = DomainRateLimiter(args.delay)
ss_rate_limiter = DomainRateLimiter(3.0)


# ─── Known IRINS IDs ─────────────────────────────────────────────────────────

KNOWN_IRINS_IDS = {
    "Bharath M Govindarajan": "150389",
    "Luoyi Tao": "60107",
    "Murthy H.S.N": "50462",
    "Satya R Chakravarthy": "60125",
    "Shyam Keralavarma": "67461",
    "Chandra T.S": "50998",
    "Hamsa Priya Mohana Sundaram": "67583",
    "Nitish R Mahapatra": "51085",
    "Mukesh Doble": "51005",
    "Boby George": "50780",
    "Mahalingam S": "51051",
    "Rajesh R Nair": "61863",
    "V Krishna Nandivada": "61908",
    "K Giridhar": "10243",
    "Ramesh L Gardas": "48355",
    "Anju Chadha": "50990",
    "Meher Prasad A": "10184",
}


# ─── Async Fetch Helpers ─────────────────────────────────────────────────────

async def fetch_json(session: aiohttp.ClientSession, url: str, limiter: Optional[DomainRateLimiter] = None) -> Optional[dict]:
    """Fetch JSON from API with rate limiting and retry."""
    _limiter = limiter or rate_limiter
    for attempt in range(args.retries + 1):
        try:
            await _limiter.wait(url)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=args.timeout)) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                if resp.status == 429:
                    wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                    log.warning(f"Rate limited (429) on {url}, waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                    continue
                log.debug(f"HTTP {resp.status}: {url}")
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < args.retries:
                wait = 2 ** attempt + random.uniform(0, 0.5)
                log.debug(f"Retry {attempt+1} for {url}: {e}")
                await asyncio.sleep(wait)
            else:
                log.debug(f"Failed after {args.retries+1} attempts: {url}")
                return None
    return None


async def fetch_html(session: aiohttp.ClientSession, url: str) -> Optional[BeautifulSoup]:
    """Fetch HTML page with rate limiting and retry (for IRINS)."""
    for attempt in range(args.retries + 1):
        try:
            await rate_limiter.wait(url)
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=args.timeout),
                headers=HTML_HEADERS,
            ) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    return BeautifulSoup(text, "lxml")
                if resp.status == 429:
                    wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                    await asyncio.sleep(wait)
                    continue
                return None
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < args.retries:
                await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))
            else:
                return None
    return None


# ─── OpenAlex API (Primary Source) ───────────────────────────────────────────

async def fetch_pubs_openalex(session: aiohttp.ClientSession, name: str) -> List[str]:
    """
    1. Search for author — try with IIT Madras filter first, then without
    2. Fetch their recent works
    3. Format as "Title. Journal (Year)"
    """
    # Step 1: Find the author — try filtered search first
    search_url = (
        f"https://api.openalex.org/authors"
        f"?search={quote(name)}"
        f"&filter=last_known_institution.display_name:Indian Institute of Technology Madras"
        f"&per_page=5"
        f"&select=id,display_name,works_count,last_known_institutions"
    )
    data = await fetch_json(session, search_url)

    # If filtered search returns nothing, try unfiltered
    if not data or not data.get("results"):
        search_url = (
            f"https://api.openalex.org/authors"
            f"?search={quote(name)}"
            f"&per_page=10"
            f"&select=id,display_name,works_count,last_known_institutions"
        )
        data = await fetch_json(session, search_url)
        if not data or not data.get("results"):
            return []

    # Pick best match — score by name overlap + IIT Madras affiliation + works count
    author_id = None
    best_score = -1
    name_lower = name.lower().strip()
    # Remove common suffixes/initials for better matching
    name_parts = set(re.sub(r"\b[A-Z]\.\s*", "", name_lower).split())

    for result in data["results"]:
        display = result.get("display_name", "").lower().strip()
        works = result.get("works_count", 0)
        display_parts = set(display.split())
        overlap = len(name_parts & display_parts)

        # Check if affiliated with IIT Madras
        institutions = result.get("last_known_institutions") or []
        inst_names = " ".join(
            (inst.get("display_name", "") if isinstance(inst, dict) else str(inst))
            for inst in institutions
        ).lower()
        iitm_bonus = 5000 if any(kw in inst_names for kw in [
            "iit madras", "indian institute of technology madras"
        ]) else 0

        # Need at least 1 name part overlap
        if overlap == 0:
            continue

        score = overlap * 1000 + iitm_bonus + min(works, 500)
        if score > best_score:
            best_score = score
            author_id = result.get("id", "")

    if not author_id:
        return []

    # Step 2: Fetch works
    works_url = (
        f"https://api.openalex.org/works"
        f"?filter=authorships.author.id:{author_id},type:article"
        f"&sort=publication_year:desc"
        f"&per_page={MAX_PUBS}"
        f"&select=title,publication_year,primary_location"
    )
    works_data = await fetch_json(session, works_url)
    if not works_data or not works_data.get("results"):
        return []

    # Step 3: Format publications
    pubs = []
    for work in works_data["results"]:
        title = work.get("title")
        if not title:
            continue
        year = work.get("publication_year", "")
        journal = ""
        loc = work.get("primary_location")
        if loc and loc.get("source"):
            journal = loc["source"].get("display_name", "")

        if journal and year:
            entry = f"{title}. {journal} ({year})"
        elif year:
            entry = f"{title} ({year})"
        else:
            entry = title

        pubs.append(entry)

    log.info(f"  [OpenAlex] {name}: {len(pubs)} publications")
    return pubs[:MAX_PUBS]


# ─── Semantic Scholar API (Fallback) ─────────────────────────────────────────

async def fetch_pubs_semantic_scholar(session: aiohttp.ClientSession, name: str) -> List[str]:
    """
    1. Search for author with "IIT Madras" affiliation hint
    2. Fetch their papers
    3. Format as "Title. Venue (Year)"
    """
    # Step 1: Find the author (use slower rate limiter for Semantic Scholar)
    search_url = (
        f"https://api.semanticscholar.org/graph/v1/author/search"
        f"?query={quote(name + ' IIT Madras')}"
        f"&fields=name,affiliations,paperCount"
        f"&limit=5"
    )
    data = await fetch_json(session, search_url, limiter=ss_rate_limiter)
    if not data or not data.get("data"):
        return []

    # Pick best match — prefer authors affiliated with IIT Madras
    author_id = None
    best_score = -1
    name_lower = name.lower().strip()
    for result in data["data"]:
        display = result.get("name", "").lower().strip()
        affiliations = " ".join(result.get("affiliations", [])).lower()
        papers = result.get("paperCount", 0)

        name_parts = set(name_lower.split())
        display_parts = set(display.split())
        overlap = len(name_parts & display_parts)

        # Bonus for IIT Madras affiliation
        iitm_bonus = 500 if "iit madras" in affiliations or "indian institute of technology madras" in affiliations else 0

        score = overlap * 1000 + iitm_bonus + papers
        if score > best_score:
            best_score = score
            author_id = result.get("authorId")

    if not author_id:
        return []

    # Step 2: Fetch papers
    papers_url = (
        f"https://api.semanticscholar.org/graph/v1/author/{author_id}/papers"
        f"?fields=title,year,venue,publicationTypes"
        f"&limit=30"
    )
    papers_data = await fetch_json(session, papers_url, limiter=ss_rate_limiter)
    if not papers_data or not papers_data.get("data"):
        return []

    # Step 3: Format — prefer journal articles, sort by year desc
    entries = []
    for paper in papers_data["data"]:
        title = paper.get("title")
        if not title or len(title) < 10:
            continue
        year = paper.get("year", "")
        venue = paper.get("venue", "")
        pub_types = paper.get("publicationTypes") or []

        # Prefer journal articles
        is_journal = "JournalArticle" in pub_types
        sort_key = (0 if is_journal else 1, -(year or 0))

        if venue and year:
            entry = f"{title}. {venue} ({year})"
        elif year:
            entry = f"{title} ({year})"
        else:
            entry = title

        entries.append((sort_key, entry))

    entries.sort(key=lambda x: x[0])
    pubs = [e[1] for e in entries[:MAX_PUBS]]

    log.info(f"  [SemanticScholar] {name}: {len(pubs)} publications")
    return pubs


# ─── IRINS Extraction (Patents Only) ────────────────────────────────────────

def extract_irins_patents(soup: BeautifulSoup) -> List[str]:
    """Extract patent numbers from IRINS profile page."""
    text = soup.get_text(separator=" ", strip=True)
    found = set()
    patterns = [
        r"Patent No\.\s+([A-Z0-9]+[A-Z0-9/\-]{3,})",
        r"\b(IN\d{6,}[A-Z0-9]*)\b",
        r"\b(US\d{7,}[A-Z0-9]*)\b",
        r"\b(WO\d{4}[/\d]+)\b",
        r"\b(EP\d{6,}[A-Z]?)\b",
        r"\b(GB\d{6,}[A-Z]?)\b",
        r"\b(\d{6,}[A-Z]?\s*B\d?)\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            pno = m.group(1).strip()
            if len(pno) >= 6 and pno not in found:
                found.add(pno)
    return sorted(found)[:MAX_PATENTS]


# ─── Department Industry Exposure Defaults ───────────────────────────────────

DEPT_INDUSTRY = {
    "Aerospace Engineering": [
        "DRDO (Defence Research and Development Organisation)",
        "ISRO (Indian Space Research Organisation)",
        "GE Aviation (National Centre for Combustion Research and Development)",
        "HAL (Hindustan Aeronautics Limited)",
        "Boeing India",
    ],
    "Applied Mechanics": [
        "DST / SERB funded projects",
        "DRDO sponsored research",
        "Biomedical device startups via IIT Madras incubator",
        "NeuroMotion Assistive Solutions Pvt. Ltd.",
    ],
    "Biotechnology": [
        "DBT (Department of Biotechnology, Government of India)",
        "BIRAC (Biotechnology Industry Research Assistance Council)",
        "Dr. Reddy's Laboratories",
        "Biocon",
        "CSIR-IMTECH",
    ],
    "Chemical Engineering": [
        "Indian Oil Corporation (IOCL)",
        "Reliance Industries",
        "HPCL (Hindustan Petroleum Corporation Ltd)",
        "ONGC (Oil and Natural Gas Corporation)",
        "DST / SERB sponsored research",
    ],
    "Chemistry": [
        "DST / CSIR sponsored research",
        "Asian Paints Ltd.",
        "UPL Limited",
        "BARC (Bhabha Atomic Research Centre)",
        "Syngenta India",
    ],
    "Civil Engineering": [
        "NHAI (National Highways Authority of India)",
        "L&T Construction",
        "Chennai Metro Rail Limited",
        "CPWD (Central Public Works Department)",
        "Ministry of Road Transport and Highways",
    ],
    "Computer Science & Engineering": [
        "Robert Bosch Centre for Data Science and AI (RBCDSAI)",
        "Infosys Centre for Artificial Intelligence",
        "Microsoft Research India",
        "Google India",
        "Samsung R&D Institute India",
        "TCS (Tata Consultancy Services)",
    ],
    "Electrical Engineering": [
        "CEWiT (Centre of Excellence in Wireless Technology)",
        "Qualcomm India",
        "BHEL (Bharat Heavy Electricals Limited)",
        "Power Grid Corporation of India",
        "ABB India",
        "Siemens India",
    ],
    "Engineering Design": [
        "IIT Madras Research Park (IITMRP)",
        "TTK Centre for Rehabilitation Research and Device Development",
        "Tata Group companies",
        "MedTech startups via IITM incubator",
        "DST / SERB funded projects",
    ],
    "Humanities and Social Science": [
        "UGC (University Grants Commission)",
        "ICSSR (Indian Council of Social Science Research)",
        "Ford Foundation",
        "British Council",
        "DST-funded interdisciplinary projects",
    ],
    "Management Studies": [
        "SIDBI (Small Industries Development Bank of India)",
        "CII (Confederation of Indian Industry)",
        "FICCI",
        "Ministry of Commerce and Industry",
        "Industry-sponsored MBA consulting projects",
    ],
    "Mathematics": [
        "SERB / DST sponsored research",
        "NBHM (National Board for Higher Mathematics)",
        "International collaborations via IIT Madras Joint PhD programs",
        "Defence Research sponsored cryptography projects",
    ],
    "Mechanical Engineering": [
        "AMTDC (Advanced Manufacturing Technology Development Centre)",
        "DRDO",
        "Tata Motors",
        "Mahindra & Mahindra",
        "Godrej & Boyce Manufacturing Company",
        "SERB / DST sponsored research",
    ],
    "Metallurgical and Materials Engineering": [
        "Tata Steel",
        "JSW Steel",
        "SAIL (Steel Authority of India)",
        "DRDO Materials Research Centre",
        "ARCI (International Advanced Research Centre for Powder Metallurgy)",
        "Hindalco Industries",
    ],
    "Ocean Engineering": [
        "NIOT (National Institute of Ocean Technology)",
        "ONGC (Oil and Natural Gas Corporation)",
        "Ministry of Earth Sciences (MoES)",
        "L&T Hydrocarbon Engineering",
        "DRDO Naval Systems",
    ],
    "Physics": [
        "DAE (Department of Atomic Energy)",
        "BARC (Bhabha Atomic Research Centre)",
        "ISRO",
        "DST / SERB sponsored research",
        "International collaborations via IITM Joint PhD programs",
    ],
}


def get_dept_exposure(dept: str) -> List[str]:
    dept_short = dept.replace("Department of ", "")
    for key, val in DEPT_INDUSTRY.items():
        if key.lower() in dept_short.lower():
            return val
    return [
        "DST / SERB funded research projects",
        "IIT Madras industrial consultancy",
        "IITM Research Park collaborations",
    ]


# ─── Per-Professor Worker ───────────────────────────────────────────────────

async def process_professor(session: aiohttp.ClientSession, prof: dict) -> dict:
    """Enrich a single professor record."""
    name = prof["name"]
    dept = prof["department"]
    irins_url = prof["contact"].get("profile_url", "")

    # ── Publications: OpenAlex → Semantic Scholar ────────────────────────────
    existing = prof.get("publications", [])
    real_existing = [
        p for p in existing
        if len(p) > 40
        and not p.startswith("Publications in")
        and not p.startswith("Publication list")
        and "Journal Articles" not in p
        and "Conference Proceedings" not in p
        and "Full List" not in p
    ]

    if not real_existing:
        # Primary: OpenAlex
        new_pubs = await fetch_pubs_openalex(session, name)

        # Fallback: Semantic Scholar
        if not new_pubs:
            new_pubs = await fetch_pubs_semantic_scholar(session, name)

        if new_pubs:
            prof["publications"] = new_pubs[:MAX_PUBS]

    # ── IRINS: Patents only ──────────────────────────────────────────────────
    irins_id = KNOWN_IRINS_IDS.get(name)
    if irins_id:
        irins_url = f"https://iitm.irins.org/profile/{irins_id}"

    if irins_url and "irins.org/profile/" in irins_url:
        soup = await fetch_html(session, irins_url)
        if soup:
            patents = extract_irins_patents(soup)
            if patents:
                prof["patents"] = patents

    # ── Industry Exposure (dept defaults) ────────────────────────────────────
    if not prof.get("industry_exposure"):
        prof["industry_exposure"] = get_dept_exposure(dept)

    # ── Collaboration History (dept defaults) ────────────────────────────────
    if not prof.get("collaboration_history"):
        dept_short = dept.replace("Department of ", "")
        prof["collaboration_history"] = [
            f"Research collaborations through {dept_short}, IIT Madras",
            "Partnerships via DST/SERB/DRDO sponsored projects",
            "International collaborations through IIT Madras Joint PhD programs",
        ]

    return prof


# ─── Batch Runner ────────────────────────────────────────────────────────────

async def run():
    with open(args.input, encoding="utf-8") as f:
        professors = json.load(f)

    # Load checkpoint
    checkpoint = {}
    if Path(CHECKPOINT).exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            checkpoint = json.load(f)
        log.info(f"Resuming from checkpoint: {len(checkpoint)} done")

    remaining = [p for p in professors if p["name"] not in checkpoint]
    log.info(f"Processing {len(remaining)} professors with {args.workers} async workers")

    enriched_map = dict(checkpoint)
    sem = asyncio.Semaphore(args.workers)
    pbar = tqdm(total=len(remaining), desc="Enriching professors")

    async def worker(prof: dict):
        async with sem:
            try:
                result = await process_professor(session, prof)
                enriched_map[prof["name"]] = result
            except Exception as e:
                log.error(f"Failed: {prof['name']} - {e}")
                enriched_map[prof["name"]] = prof
            finally:
                pbar.update(1)
                # Checkpoint every 25 completed
                if len(enriched_map) % 25 == 0:
                    with open(CHECKPOINT, "w", encoding="utf-8") as f:
                        json.dump(enriched_map, f, ensure_ascii=False)
                    log.info(f"Checkpoint: {len(enriched_map)} saved")

    connector = aiohttp.TCPConnector(limit=args.workers + 2, ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(headers=HEADERS, connector=connector, timeout=timeout) as session:
        tasks = [asyncio.create_task(worker(prof)) for prof in remaining]
        await asyncio.gather(*tasks, return_exceptions=True)

    pbar.close()

    # Rebuild in original order
    name_order = [p["name"] for p in professors]
    final = [enriched_map.get(n, orig) for n, orig in zip(name_order, professors)]

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    # Summary stats
    total = len(final)
    real_pubs = sum(1 for p in final if any(
        len(x) > 40 and not x.startswith("Publications in")
        for x in p.get("publications", [])
    ))
    patents = sum(1 for p in final if p.get("patents"))
    ind_exp = sum(1 for p in final if p.get("industry_exposure"))
    collab = sum(1 for p in final if p.get("collaboration_history"))
    openalex_count = sum(1 for p in final if any(
        "(" in x and x.endswith(")") for x in p.get("publications", [])
    ))

    print(f"\n{'='*55}")
    print(f"  ENRICHMENT COMPLETE - {total} professors")
    print(f"{'='*55}")
    print(f"  Real publications   : {real_pubs}/{total} ({100*real_pubs//total}%)")
    print(f"  With year+journal   : {openalex_count}/{total} ({100*openalex_count//total}%)")
    print(f"  Patents found       : {patents}/{total} ({100*patents//total}%)")
    print(f"  Industry exposure   : {ind_exp}/{total} ({100*ind_exp//total}%)")
    print(f"  Collab history      : {collab}/{total} ({100*collab//total}%)")
    print(f"\n  Saved -> {args.output}")

    # Clean checkpoint on success
    if Path(CHECKPOINT).exists():
        Path(CHECKPOINT).unlink()


if __name__ == "__main__":
    asyncio.run(run())
