"""
CollabV AI - Abstract fetcher for patent listings.

Two source strategy, primary -> fallback:

1. IITM TTO page (when listing.idf_url is set; ~50-60% of seeded listings).
   - Source-of-truth for IITM patents, no bot block, canonical wording.

2. Google Patents search by title + inventor (fallback when no idf_url).
   - Reuses the existing patent_scraper Google Patents XHR approach.
   - Aggressive rate limiting + retries because Google Patents has bot protection.

A FetchResult dataclass carries the source + reason for empty fetches so the
caller (and the backfill script's audit log) can distinguish "abstract not
found anywhere" from "all upstream sources rate-limited us".

Rate limits + backoff are configurable; defaults are polite (2 s/req for IITM,
3 s/req for Google Patents, exponential backoff to 60 s on 429/503).
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


@dataclass
class FetchResult:
    """Result of an abstract-fetch attempt.

    abstract is non-None iff a real abstract was retrieved. source identifies
    which upstream answered. error is set on failure so the audit log records
    the reason without raising.
    """
    abstract: Optional[str] = None
    source: str = "none"             # "iitm_tto" | "google_patents" | "none"
    error: Optional[str] = None
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.abstract and len(self.abstract.strip()) >= 50)


@dataclass
class FetcherConfig:
    iitm_delay_sec: float = 2.0
    google_delay_sec: float = 3.0
    request_timeout_sec: float = 30.0
    max_retries: int = 3
    backoff_initial_sec: float = 5.0
    backoff_max_sec: float = 60.0
    min_abstract_len: int = 50          # below this, treat as "not really an abstract"
    max_abstract_len: int = 5000        # truncate ultra-long


class AbstractFetcher:
    """Multi-source abstract fetcher. Holds the aiohttp session across calls
    so connection pooling / cookies persist (helpful with Google Patents)."""

    def __init__(self, config: Optional[FetcherConfig] = None) -> None:
        self.config = config or FetcherConfig()
        self._session = None     # lazy aiohttp.ClientSession
        self._last_iitm_call = 0.0
        self._last_google_call = 0.0

    async def __aenter__(self) -> "AbstractFetcher":
        import aiohttp
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.request_timeout_sec),
            headers={"User-Agent": USER_AGENT,
                     "Accept-Language": "en-US,en;q=0.9"},
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._session:
            await self._session.close()

    async def fetch(
        self,
        title: str,
        idf_url: Optional[str] = None,
        inventor_name: Optional[str] = None,
        patent_number: Optional[str] = None,
    ) -> FetchResult:
        """Try each source until one returns an abstract."""
        t0 = time.time()
        # Primary: IITM TTO page if idf_url present.
        if idf_url:
            await self._respect_rate("iitm")
            result = await self._fetch_iitm_tto(idf_url)
            result.latency_ms = (time.time() - t0) * 1000
            if result.ok:
                return result
        # Fallback: Google Patents search.
        if title:
            await self._respect_rate("google")
            result = await self._fetch_google_patents(title, inventor_name)
            result.latency_ms = (time.time() - t0) * 1000
            if result.ok:
                return result
        # All sources failed.
        return FetchResult(
            abstract=None, source="none",
            error="all upstream sources returned no abstract",
            latency_ms=(time.time() - t0) * 1000,
        )

    # ─── Rate limiting ──────────────────────────────────────────────────

    async def _respect_rate(self, source: str) -> None:
        if source == "iitm":
            min_gap = self.config.iitm_delay_sec
            last = self._last_iitm_call
        else:
            min_gap = self.config.google_delay_sec
            last = self._last_google_call
        elapsed = time.time() - last
        if elapsed < min_gap:
            await asyncio.sleep(min_gap - elapsed)
        if source == "iitm":
            self._last_iitm_call = time.time()
        else:
            self._last_google_call = time.time()

    # ─── Source 1: IITM TTO page ────────────────────────────────────────

    async def _fetch_iitm_tto(self, url: str) -> FetchResult:
        """Scrape an ip.iitm.ac.in technologies-portfolio page for the abstract.

        These pages aren't documented but consistently have an "Abstract" or
        "Description" heading followed by a paragraph (or several). We grab
        the first meaningful prose block.
        """
        if "ip.iitm.ac.in" not in url:
            return FetchResult(source="iitm_tto", error="URL is not an IITM TTO page")
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return FetchResult(source="iitm_tto", error="bs4 not installed")

        text = await self._get_with_backoff(url, "iitm")
        if text is None:
            return FetchResult(source="iitm_tto", error="upstream fetch failed")
        soup = BeautifulSoup(text, "html.parser")

        # Strategy: look for headings or labels containing "abstract" /
        # "description" / "overview" and pull the next sibling text. If none,
        # take the longest <p> in the main content area.
        abstract = ""
        for label in ("abstract", "description", "overview", "background",
                      "summary", "technology"):
            heading = soup.find(
                lambda tag: tag.name in {"h1", "h2", "h3", "h4", "h5", "strong", "b"}
                and label in (tag.get_text(strip=True) or "").lower()
            )
            if heading:
                # Walk forward to find the next paragraph(s)
                parts = []
                nxt = heading.find_next()
                steps = 0
                while nxt and steps < 8:
                    if nxt.name in {"p", "div"}:
                        t = nxt.get_text(" ", strip=True)
                        if t and len(t) >= 40:
                            parts.append(t)
                    if nxt.name in {"h1", "h2", "h3", "h4", "h5"}:
                        break
                    nxt = nxt.find_next()
                    steps += 1
                if parts:
                    abstract = "  ".join(parts)
                    break

        if not abstract:
            # Fallback: longest <p> on the page
            paras = [(len(p.get_text(strip=True)), p.get_text(" ", strip=True))
                     for p in soup.find_all("p")]
            paras = [p for p in paras if p[0] >= 80]
            if paras:
                paras.sort(reverse=True)
                abstract = paras[0][1]

        if not abstract:
            return FetchResult(source="iitm_tto",
                               error="no abstract-shaped content on page")

        abstract = self._clean_abstract(abstract)
        if len(abstract) < self.config.min_abstract_len:
            return FetchResult(source="iitm_tto",
                               error=f"abstract too short ({len(abstract)} chars)")
        return FetchResult(abstract=abstract, source="iitm_tto")

    # ─── Source 2: Google Patents ───────────────────────────────────────

    async def _fetch_google_patents(
        self, title: str, inventor: Optional[str] = None,
    ) -> FetchResult:
        """Search Google Patents by title + inventor, fetch the top result's
        page, scrape the abstract.

        Google Patents is bot-protected; this can fail. The audit log records
        the failure reason so the user knows whether to widen sources.
        """
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            return FetchResult(source="google_patents", error="bs4 not installed")

        # Build the search URL. Quoting title raises precision a lot.
        q_parts = [f'"{title.strip()[:120]}"']
        if inventor:
            q_parts.append(f"inventor:({inventor.strip()})")
        q_parts.append("assignee:(IIT+Madras)")
        q = "+".join(q_parts).replace(" ", "+")
        search_url = f"https://patents.google.com/?q={q}"

        text = await self._get_with_backoff(search_url, "google")
        if text is None:
            return FetchResult(source="google_patents", error="search request failed")
        soup = BeautifulSoup(text, "html.parser")

        # Find the first result's link. Google Patents results use
        # <state-modifier> tags with data-result attribute. Fallback: any
        # anchor pointing at /patent/.
        patent_links = []
        for a in soup.find_all("a"):
            href = a.get("href", "")
            if "/patent/" in href and not patent_links:
                # Normalize relative urls
                if href.startswith("/"):
                    href = "https://patents.google.com" + href
                patent_links.append(href)
        if not patent_links:
            return FetchResult(source="google_patents",
                               error="no patent links in search results "
                                     "(bot-blocked or no match)")

        # Fetch the first result
        patent_url = patent_links[0]
        text = await self._get_with_backoff(patent_url, "google")
        if text is None:
            return FetchResult(source="google_patents",
                               error="patent page fetch failed")
        soup = BeautifulSoup(text, "html.parser")

        # The abstract is in <section itemprop="abstract"> or <abstract>
        abstract = ""
        node = soup.find(attrs={"itemprop": "abstract"})
        if node:
            abstract = node.get_text(" ", strip=True)
        if not abstract:
            node = soup.find("abstract")
            if node:
                abstract = node.get_text(" ", strip=True)
        if not abstract:
            # Last resort: <meta name="description">
            meta = soup.find("meta", attrs={"name": "description"})
            if meta and meta.get("content"):
                abstract = meta["content"]

        abstract = self._clean_abstract(abstract)
        if not abstract or len(abstract) < self.config.min_abstract_len:
            return FetchResult(source="google_patents",
                               error=f"abstract too short or empty "
                                     f"({len(abstract or '')} chars)")
        return FetchResult(abstract=abstract, source="google_patents")

    # ─── HTTP + backoff ─────────────────────────────────────────────────

    async def _get_with_backoff(self, url: str, source: str) -> Optional[str]:
        """GET with exponential backoff on 429/503. Returns body text or None."""
        cfg = self.config
        delay = cfg.backoff_initial_sec
        for attempt in range(cfg.max_retries):
            try:
                async with self._session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    if resp.status in (429, 503):
                        logger.info("%s returned %d, backing off %.0fs (attempt %d/%d)",
                                    source, resp.status, delay, attempt + 1, cfg.max_retries)
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, cfg.backoff_max_sec)
                        continue
                    logger.debug("%s returned %d for %s", source, resp.status, url)
                    return None
            except Exception as e:
                logger.debug("%s fetch error %s: %s", source, type(e).__name__, e)
                await asyncio.sleep(delay)
                delay = min(delay * 2, cfg.backoff_max_sec)
        return None

    # ─── Helpers ────────────────────────────────────────────────────────

    def _clean_abstract(self, text: str) -> str:
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text or "").strip()
        # Strip common boilerplate prefixes
        text = re.sub(r"^abstract[:\-\s]+", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^description[:\-\s]+", "", text, flags=re.IGNORECASE)
        # Truncate
        if len(text) > self.config.max_abstract_len:
            text = text[: self.config.max_abstract_len].rstrip() + "..."
        return text


# ─── Convenience function for the API BackgroundTask path ───────────────

async def fetch_abstract_for_listing(listing: Dict[str, Any]) -> FetchResult:
    """Convenience wrapper - opens its own session, single fetch.

    Used by api.py's BackgroundTask on new-listing-create. Not recommended for
    bulk operations (each call opens a fresh aiohttp session); the batch
    backfill script reuses a single session.
    """
    async with AbstractFetcher() as f:
        inventors = listing.get("inventor_names") or []
        return await f.fetch(
            title=listing.get("title", ""),
            idf_url=listing.get("idf_url"),
            inventor_name=inventors[0] if inventors else None,
            patent_number=listing.get("patent_number"),
        )


__all__ = ["AbstractFetcher", "FetcherConfig", "FetchResult",
           "fetch_abstract_for_listing"]
