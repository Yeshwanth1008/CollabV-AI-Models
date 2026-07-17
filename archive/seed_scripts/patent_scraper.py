"""
CollabV AI - Patent Scraper
=============================
Fetches patent data and attaches it to professor profiles.

The IITM Technology Transfer Office site (ip.iitm.ac.in/non-licenses) loads
data client-side. The exact API endpoint isn't published, so this scraper
tries several reasonable patterns:

  1. PRIMARY: hit a configurable IITM API endpoint (set via --api-url or
     env COLLABV_IITM_PATENT_API).
  2. FALLBACK: query Google Patents' public search endpoint by inventor name
     for each professor.
  3. SYNTHETIC: if both fail, populate professors with a deterministic but
     clearly-flagged synthetic patent set so downstream models can be
     exercised end-to-end. Synthetic records are marked with
     `"source": "synthetic"` so they can be filtered out for production.

CLI:
    python -m collabv.patent_scraper --output iitm_professors_with_patents.json
    python -m collabv.patent_scraper --api-url <URL>
    python -m collabv.patent_scraper --use-google-patents
    python -m collabv.patent_scraper --use-synthetic
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ─── Department list (IITM) ─────────────────────────────────────────────────

IITM_DEPARTMENTS = [
    "Aerospace Engineering",
    "Applied Mechanics",
    "Biotechnology",
    "Chemical Engineering",
    "Chemistry",
    "Civil Engineering",
    "Computer Science & Engineering",
    "Electrical Engineering",
    "Engineering Design",
    "Humanities and Social Sciences",
    "Management Studies",
    "Mathematics",
    "Mechanical Engineering",
    "Metallurgical and Materials Engineering",
    "Ocean Engineering",
    "Physics",
]


# ─── Name normalization ─────────────────────────────────────────────────────

_PUNCT_RE = re.compile(r"[^\w\s]")


def _name_tokens(name: str) -> list:
    """Return lowercase tokens, stripped of titles/punct, preserving order."""
    name = _PUNCT_RE.sub(" ", str(name).lower())
    return [t for t in name.split()
            if t and t not in {"dr", "prof", "mr", "ms", "mrs", "shri", "smt"}]


def _norm_name(name: str) -> str:
    """Sort-and-join normalization — order-insensitive equality check."""
    return " ".join(sorted(_name_tokens(name)))


def _initials_signature(name: str) -> str:
    """First letter of each token, sorted. Helps match 'AROCKIARAJAN A' to
    'A Arockiarajan' or 'Arockiarajan A.' to 'Arockiarajan A'."""
    return "".join(sorted(t[0] for t in _name_tokens(name) if t))


def _is_initial(tok: str) -> bool:
    return len(tok) == 1 and tok.isalpha()


def _name_overlap(inv_tokens: list, prof_tokens: list) -> float:
    """Token overlap that treats single-letter initials as compatible with any
    token starting with that letter on the other side.

    Examples that should match strongly:
      'AROCKIARAJAN A' vs 'Arockiarajan A'         -> 1.0
      'MURALEEDHARAN K M' vs 'K M Muraleedharan'   -> 1.0
      'LAKSHMAN NEELAKANTAN' vs 'Lakshman Neelakantan' -> 1.0
      'LAKSHMAN NEELAKANTAN' vs 'Lakshmi Subramanian'  -> 0.0
    """
    if not inv_tokens or not prof_tokens:
        return 0.0

    # Multi-letter tokens must overlap on the non-initial side
    inv_words = [t for t in inv_tokens if not _is_initial(t)]
    prof_words = [t for t in prof_tokens if not _is_initial(t)]
    if not inv_words or not prof_words:
        return 0.0

    word_matches = len(set(inv_words) & set(prof_words))
    # If no real words overlap, this isn't the same person no matter what
    # the initials say.
    if word_matches == 0:
        return 0.0

    # Initial-vs-word matches as a softer bonus
    inv_letters = {t for t in inv_tokens if _is_initial(t)}
    prof_letters = {t for t in prof_tokens if _is_initial(t)}
    initial_word = sum(
        1 for letter in inv_letters
        if any(w.startswith(letter) for w in prof_words)
    )
    initial_word += sum(
        1 for letter in prof_letters
        if any(w.startswith(letter) for w in inv_words)
    )

    denom = max(len(inv_words), len(prof_words))
    score = (word_matches + 0.5 * initial_word) / denom
    return min(score, 1.0)


def _match_inventor(inventor: str, professors: List[Dict[str, Any]]) -> Optional[int]:
    """Find best-matching professor index. Returns None below threshold.

    Strategy:
      1. Exact normalized match.
      2. Token overlap with initial-awareness.
      3. Single-surname fallback: if inventor is a single 5+ char token and
         exactly one professor has that token in their name, accept it.
    """
    if not inventor or not inventor.strip():
        return None

    inv_tokens = _name_tokens(inventor)
    if not inv_tokens:
        return None
    inv_norm = " ".join(sorted(inv_tokens))

    best_idx, best_score = None, 0.0
    for i, p in enumerate(professors):
        prof_name = p.get("name") or ""
        prof_tokens = _name_tokens(prof_name)
        if not prof_tokens:
            continue
        if " ".join(sorted(prof_tokens)) == inv_norm:
            return i
        s = _name_overlap(inv_tokens, prof_tokens)
        if s > best_score:
            best_idx, best_score = i, s

    if best_score >= 0.6:
        return best_idx

    # Single-surname fallback: 'JAYAGANTHAN' -> 'Rengaswamy Jayaganthan' if
    # that token uniquely identifies one professor. Skip single-letter and
    # very short tokens (too ambiguous).
    if len(inv_tokens) == 1 and len(inv_tokens[0]) >= 5:
        token = inv_tokens[0]
        candidates = [
            i for i, p in enumerate(professors)
            if token in _name_tokens(p.get("name") or "")
        ]
        if len(candidates) == 1:
            return candidates[0]

    return None


# ─── Strategy 1a: real IITM ip.iitm.ac.in endpoint (POST /ajax/getNonLicenses) ──

# Default to the live IITM endpoint. Override via:
#   env  COLLABV_IITM_PATENT_API=https://...
#   CLI  --api-url https://...
IITM_REAL_URL = os.environ.get(
    "COLLABV_IITM_PATENT_API",
    "https://ip.iitm.ac.in/ajax/getNonLicenses",
)


async def _fetch_iitm_real(url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Hit the actual IITM Technology Transfer Office endpoint.

    Endpoint: POST https://ip.iitm.ac.in/ajax/getNonLicenses
    Body:     for=table  (form-urlencoded)
    Returns:  {"status":1,"code":200,"data":[ {idf_code, idf_title,
              department_name, professor_name (CAPS), patent_status,
              patent_type, idf_year, lic_date, idf_url}, ... ]}

    The site returns the entire dataset in one call; pagination/filter is
    client-side. If the request fails (WAF block, network), returns [].
    """
    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp not installed; cannot fetch real IITM endpoint")
        return []

    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Origin": "https://ip.iitm.ac.in",
        "Referer": "https://ip.iitm.ac.in/non-licenses",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 "
            "Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
    }

    target = url or IITM_REAL_URL
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=60),
        headers=headers,
    ) as session:
        try:
            async with session.post(target, data={"for": "table"}) as resp:
                if resp.status != 200:
                    logger.warning("IITM endpoint returned %d", resp.status)
                    return []
                payload = await resp.json(content_type=None)
        except Exception as e:
            logger.warning("IITM fetch failed: %s", e)
            return []

    if not isinstance(payload, dict) or payload.get("status") != 1:
        logger.warning("IITM endpoint returned unexpected payload: %s",
                       str(payload)[:200])
        return []

    raw_records = payload.get("data", [])
    if not isinstance(raw_records, list):
        return []

    records: List[Dict[str, Any]] = []
    for r in raw_records:
        if not isinstance(r, dict):
            continue
        # Normalize lic_date "14-Aug-2024" -> "2024-08-14" if possible.
        filing_date = r.get("lic_date") or ""
        iso_date = _to_iso_date(filing_date)
        records.append({
            "title": r.get("idf_title", "").strip(),
            "filing_date": iso_date or filing_date,
            "year": r.get("idf_year"),
            "patent_number": str(r.get("idf_code") or ""),
            "status": _normalize_status(r.get("patent_status", "")),
            "patent_type": r.get("patent_type") or "PATENT",
            "abstract": "",                     # API doesn't return abstracts
            "inventors": [r.get("professor_name", "").strip()],
            "department": r.get("department_name", "").strip(),
            "idf_url": r.get("idf_url"),
            "source": "iitm",
        })
    logger.info("Fetched %d patents from real IITM endpoint", len(records))
    return records


def _to_iso_date(text: str) -> Optional[str]:
    """Convert "14-Aug-2024" -> "2024-08-14". Returns None if format unknown."""
    if not text:
        return None
    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    parts = text.split("-")
    if len(parts) == 3:
        d, mon, y = parts
        m = months.get(mon[:3].lower())
        if m and d.isdigit() and y.isdigit():
            return f"{y}-{m}-{int(d):02d}"
    return None


def _normalize_status(raw: str) -> str:
    """Map IITM status strings to our canonical {granted, published, filed}."""
    lower = (raw or "").lower()
    if "grant" in lower:
        return "granted"
    if "publish" in lower:
        return "published"
    # "Pending - External", "Pending - Internal", etc. all map to filed.
    return "filed"


# ─── Strategy 1b: configurable generic IITM-like API ────────────────────────

async def _fetch_iitm_api(api_url: str, departments: List[str]) -> List[Dict[str, Any]]:
    """Try to fetch patents from a configurable IITM API endpoint.

    Strategy: iterate departments, POST/GET with department as param. We probe
    both query-param and form-data styles. Rate-limited at 1 req/s.
    """
    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp not installed; cannot fetch from IITM API")
        return []

    patents: List[Dict[str, Any]] = []
    seen: set = set()

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        headers={"User-Agent": "CollabV-AI-PatentScraper/1.0"},
    ) as session:
        for dept in departments:
            try:
                # Try GET with department query
                async with session.get(api_url, params={"department": dept}) as resp:
                    if resp.status >= 400:
                        logger.warning("API returned %d for %s", resp.status, dept)
                        await asyncio.sleep(1.0)
                        continue
                    raw = await resp.text()
            except Exception as e:
                logger.warning("Fetch failed for %s: %s", dept, e)
                await asyncio.sleep(1.0)
                continue

            records = _parse_iitm_response(raw, dept)
            for rec in records:
                key = (rec.get("patent_number") or rec.get("title", ""), rec.get("title", ""))
                if key in seen:
                    continue
                seen.add(key)
                patents.append(rec)

            await asyncio.sleep(1.0)  # rate limit

    return patents


def _parse_iitm_response(raw: str, department: str) -> List[Dict[str, Any]]:
    """Heuristically parse an IITM API response: try JSON, then HTML table fallback."""
    records: List[Dict[str, Any]] = []
    raw = raw.strip()
    if not raw:
        return records

    # Try JSON first
    if raw.startswith("{") or raw.startswith("["):
        try:
            data = json.loads(raw)
            for item in (data if isinstance(data, list) else data.get("data", data.get("patents", []))):
                if not isinstance(item, dict):
                    continue
                records.append({
                    "title": item.get("title") or item.get("patent_title") or item.get("name", ""),
                    "filing_date": item.get("filing_date") or item.get("date") or item.get("filed_on", ""),
                    "patent_number": item.get("patent_number") or item.get("application_number") or item.get("number", ""),
                    "status": item.get("status") or "filed",
                    "abstract": item.get("abstract") or item.get("description", ""),
                    "inventors": _split_inventors(item.get("inventors") or item.get("pi") or item.get("investigators", "")),
                    "department": department,
                    "source": "iitm",
                })
            return records
        except json.JSONDecodeError:
            pass

    # HTML fallback - very crude
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        return records

    soup = BeautifulSoup(raw, "lxml")
    for row in soup.select("table tr"):
        cells = [c.get_text(strip=True) for c in row.find_all("td")]
        if len(cells) < 2:
            continue
        records.append({
            "title": cells[0] if cells else "",
            "filing_date": cells[1] if len(cells) > 1 else "",
            "patent_number": cells[2] if len(cells) > 2 else "",
            "status": cells[3] if len(cells) > 3 else "filed",
            "inventors": _split_inventors(cells[4] if len(cells) > 4 else ""),
            "department": department,
            "source": "iitm",
        })
    return records


def _split_inventors(raw: Any) -> List[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    if isinstance(raw, str):
        return [x.strip() for x in re.split(r"[,;/&]+| and ", raw) if x.strip()]
    return []


# ─── Strategy 2: Google Patents search by inventor ──────────────────────────

async def _fetch_google_patents(professors: List[Dict[str, Any]], limit_per_prof: int = 5) -> List[Dict[str, Any]]:
    """Use Google Patents' public search HTML to find patents by inventor.

    This is not an officially supported API. Use respectfully: 1 req/s, custom
    User-Agent. Stops early on errors.
    """
    try:
        import aiohttp
        from bs4 import BeautifulSoup  # type: ignore
    except ImportError:
        logger.warning("aiohttp/bs4 not installed; skipping Google Patents")
        return []

    patents: List[Dict[str, Any]] = []
    seen: set = set()
    base_url = "https://patents.google.com/xhr/query"

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=20),
        headers={"User-Agent": "CollabV-AI-PatentScraper/1.0"},
    ) as session:
        for prof in professors:
            name = prof.get("name", "").strip()
            if not name:
                continue
            params = {
                "url": f"inventor:({name})+assignee:(IIT+Madras)",
                "exp": "",
            }
            try:
                async with session.get(base_url, params=params) as resp:
                    if resp.status >= 400:
                        logger.warning("Google Patents %d for %s", resp.status, name)
                        await asyncio.sleep(1.0)
                        continue
                    text = await resp.text()
                data = json.loads(text)
                results = (data.get("results", {})
                              .get("cluster", [{}])[0]
                              .get("result", []))[:limit_per_prof]
            except Exception as e:
                logger.debug("Google Patents fetch error for %s: %s", name, e)
                await asyncio.sleep(1.0)
                continue

            for r in results:
                pat = r.get("patent", {})
                pid = pat.get("publication_number")
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                patents.append({
                    "title": pat.get("title", ""),
                    "filing_date": pat.get("filing_date", ""),
                    "patent_number": pid,
                    "status": pat.get("kind_code_label", "published"),
                    "abstract": pat.get("snippet", ""),
                    "inventors": [name],
                    "department": prof.get("department", ""),
                    "source": "google_patents",
                })
            await asyncio.sleep(1.0)
    return patents


# ─── Strategy 3: Synthetic patents (deterministic, clearly flagged) ────────

def _generate_synthetic_patents(professors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create deterministic, realistic-looking patents for demo / testing.

    Uses each professor's research areas + a stable RNG seeded by professor_id
    so the data is reproducible. Every record has source='synthetic'.
    """
    synthetic_titles_by_field = {
        "machine learning": [
            "Method and system for efficient model inference on edge devices",
            "Neural network architecture for low-resource text classification",
            "Apparatus for adaptive learning rate optimization in deep networks",
        ],
        "robotics": [
            "Robotic gripper with adaptive surface contact",
            "System for autonomous navigation in unstructured environments",
            "Modular robot arm with reconfigurable end-effectors",
        ],
        "energy": [
            "High-efficiency solar cell with tandem absorption layers",
            "Battery management system for grid-scale storage",
            "Hydrogen production from waste biomass via electrolysis",
        ],
        "biotechnology": [
            "Engineered enzyme for cellulose degradation",
            "Microfluidic device for single-cell analysis",
            "Drug delivery system using biodegradable polymers",
        ],
        "materials": [
            "Composite material with enhanced thermal conductivity",
            "Method for additive manufacturing of titanium alloys",
            "Self-healing polymer for structural applications",
        ],
        "chemical": [
            "Catalyst for selective hydrogenation of unsaturated compounds",
            "Process for solvent-free polymerization",
            "Membrane for desalination of brackish water",
        ],
        "civil": [
            "Earthquake-resistant connection for steel structures",
            "Sustainable concrete mix with industrial by-products",
            "Real-time monitoring system for bridge structural health",
        ],
        "electrical": [
            "RF MEMS switch for reconfigurable antenna arrays",
            "Power converter for renewable energy integration",
            "Low-power IoT sensor node architecture",
        ],
        "default": [
            "Apparatus and method for improved measurement of process variables",
            "System for automated quality inspection",
            "Device for energy-efficient operation in industrial settings",
        ],
    }

    patents: List[Dict[str, Any]] = []
    today_year = time.gmtime().tm_year

    for prof in professors:
        seed = int(hashlib.sha1(str(prof.get("professor_id", prof.get("name", ""))).encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)
        # 0-4 patents per professor, weighted toward 1-2
        n_patents = rng.choices([0, 1, 2, 3, 4], weights=[20, 35, 25, 15, 5])[0]
        if n_patents == 0:
            continue

        field_key = "default"
        for area in (prof.get("research_areas") or []):
            area_l = str(area).lower()
            for key in synthetic_titles_by_field:
                if key in area_l:
                    field_key = key
                    break
            if field_key != "default":
                break

        titles = synthetic_titles_by_field[field_key] + synthetic_titles_by_field["default"]
        rng.shuffle(titles)

        for i in range(n_patents):
            year = rng.randint(today_year - 8, today_year - 1)
            month = rng.randint(1, 12)
            day = rng.randint(1, 28)
            status = rng.choices(["granted", "published", "filed"], weights=[30, 40, 30])[0]
            patents.append({
                "title": titles[i % len(titles)],
                "filing_date": f"{year}-{month:02d}-{day:02d}",
                "patent_number": f"IN{rng.randint(200000, 999999)}{rng.choice(['A', 'B'])}",
                "status": status,
                "abstract": f"Synthetic abstract for testing CollabV AI patent scoring. Related to {prof.get('department', '')}.",
                "inventors": [prof.get("name", "")],
                "department": prof.get("department", ""),
                "source": "synthetic",
            })
    return patents


# ─── Match & merge ─────────────────────────────────────────────────────────

def _attach_patents(
    professors: List[Dict[str, Any]], patents: List[Dict[str, Any]],
) -> Tuple[int, Dict[str, int]]:
    """Attach patents to professors. Returns (total matched, per-dept counts)."""
    matched_count = 0
    dept_counts: Dict[str, int] = {}

    # Ensure every professor has a patents list
    for p in professors:
        if "patents" not in p or not isinstance(p["patents"], list):
            p["patents"] = []

    for pat in patents:
        inventors = pat.get("inventors") or []
        attached = False
        for inv in inventors:
            idx = _match_inventor(inv, professors)
            if idx is not None:
                # Avoid duplicates by patent number or title
                existing_keys = {
                    (p.get("patent_number"), p.get("title"))
                    for p in professors[idx]["patents"]
                }
                key = (pat.get("patent_number"), pat.get("title"))
                if key not in existing_keys:
                    professors[idx]["patents"].append(pat)
                    attached = True
        if attached:
            matched_count += 1
            dept = pat.get("department", "Unknown")
            dept_counts[dept] = dept_counts.get(dept, 0) + 1

    return matched_count, dept_counts


# ─── Orchestrator ──────────────────────────────────────────────────────────

async def scrape_patents(
    professors_path: str,
    output_path: str,
    use_real: bool = True,
    api_url: Optional[str] = None,
    use_google_patents: bool = False,
    use_synthetic: bool = False,
    departments: Optional[List[str]] = None,
    preserve_existing: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Main entry point. Returns a summary dict.

    use_real (default True): hit the live ip.iitm.ac.in/ajax/getNonLicenses
      endpoint.  This is the canonical source.
    api_url: override - hit a different IITM-style endpoint.
    use_google_patents: secondary - fetch from Google Patents per professor.
    use_synthetic: tertiary - generate deterministic synthetic patents.
    preserve_existing: if True, don't wipe each professor's current patents
      before merging new ones. Default is False so re-runs are clean.
    """
    professors_path = str(Path(professors_path))
    with open(professors_path, encoding="utf-8") as f:
        professors = json.load(f)
    logger.info("Loaded %d professors", len(professors))

    if not preserve_existing:
        for p in professors:
            p["patents"] = []

    departments = departments or IITM_DEPARTMENTS
    all_patents: List[Dict[str, Any]] = []

    primary_url = api_url or IITM_REAL_URL
    if use_real:
        logger.info("Strategy 1: Fetching from real IITM endpoint at %s", primary_url)
        all_patents += await _fetch_iitm_real(primary_url)
        logger.info("Real IITM endpoint returned %d patents", len(all_patents))

    if not all_patents and api_url and api_url != IITM_REAL_URL:
        logger.info("Strategy 1b: Fetching from configured API at %s", api_url)
        all_patents += await _fetch_iitm_api(api_url, departments)

    if not all_patents and use_google_patents:
        logger.info("Strategy 2: Querying Google Patents per professor")
        all_patents += await _fetch_google_patents(professors)

    if not all_patents and use_synthetic:
        logger.info("Strategy 3: Generating synthetic patents for testing")
        all_patents += _generate_synthetic_patents(professors)
        logger.info("Generated %d synthetic patents", len(all_patents))

    if dry_run:
        # Print the first 3 parsed records and exit without touching disk.
        print()
        print(f"=== DRY RUN — {len(all_patents)} patents parsed ===")
        print(f"  Source URL  : {primary_url}")
        print(f"  Will NOT write {output_path}")
        print()
        for i, rec in enumerate(all_patents[:3], start=1):
            print(f"--- Record {i} ---")
            print(json.dumps(rec, indent=2, ensure_ascii=False))
            print()
        if all_patents:
            sample = all_patents[0]
            print("Field map (parsed key -> sample value):")
            for k, v in sample.items():
                vs = str(v)
                if len(vs) > 60:
                    vs = vs[:57] + "..."
                print(f"  {k:18} -> {vs}")
        # Also run the matcher in-memory so the user sees match rate without
        # touching any files.
        matched, dept_counts = _attach_patents(
            [dict(p) for p in professors], all_patents
        )
        return {
            "dry_run": True,
            "total_patents": len(all_patents),
            "matched_patents": matched,
            "source_url": primary_url,
            "output_file": None,
        }

    matched, dept_counts = _attach_patents(professors, all_patents)

    # Write outputs
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(professors, f, indent=2, ensure_ascii=False)

    patents_file = str(Path(output_path).parent / "iitm_patents.json")
    with open(patents_file, "w", encoding="utf-8") as f:
        json.dump(all_patents, f, indent=2, ensure_ascii=False)

    professors_with_patents = sum(1 for p in professors if p.get("patents"))

    summary = {
        "total_patents": len(all_patents),
        "matched_patents": matched,
        "professors_with_patents": professors_with_patents,
        "by_department": dict(sorted(dept_counts.items(), key=lambda x: -x[1])),
        "output_file": output_path,
        "patents_file": patents_file,
    }
    return summary


def _main() -> None:
    parser = argparse.ArgumentParser(description="CollabV AI patent scraper")
    parser.add_argument("--input", default=str(Path(__file__).parent.parent / "iitm_professors_nlp.backup.json"))
    parser.add_argument("--output", default=str(Path(__file__).parent.parent / "iitm_professors_with_patents.json"))
    parser.add_argument("--no-real", action="store_true",
                        help="Skip the real IITM endpoint (default: enabled)")
    parser.add_argument("--api-url", default=os.environ.get("COLLABV_IITM_PATENT_API"))
    parser.add_argument("--use-google-patents", action="store_true")
    parser.add_argument("--use-synthetic", action="store_true",
                        help="If primary sources fail, fill with synthetic patents")
    parser.add_argument("--preserve-existing", action="store_true",
                        help="Merge new patents with existing ones instead of replacing")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch + parse but DO NOT write output files; "
                             "print the first 3 parsed records for inspection")
    args = parser.parse_args()

    summary = asyncio.run(scrape_patents(
        professors_path=args.input,
        output_path=args.output,
        use_real=not args.no_real,
        api_url=args.api_url,
        use_google_patents=args.use_google_patents,
        use_synthetic=args.use_synthetic,
        preserve_existing=args.preserve_existing,
        dry_run=args.dry_run,
    ))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    _main()


__all__ = ["scrape_patents", "IITM_DEPARTMENTS"]
