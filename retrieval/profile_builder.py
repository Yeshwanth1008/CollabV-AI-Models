"""
Online pipeline — enriches a professor profile with live API data
from OpenAlex, Semantic Scholar, ORCID, CrossRef, and arXiv, plus NER extraction.
"""

import asyncio
import re
import ssl
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

import aiohttp

# ── NER patterns for skill/method/domain extraction ──────────────────────

TECH_KEYWORDS = {
    "python", "matlab", "tensorflow", "pytorch", "keras", "scikit-learn",
    "r", "java", "c++", "c", "fortran", "julia", "simulink", "ansys",
    "comsol", "abaqus", "openfoam", "gaussian", "vasp", "spark", "hadoop",
    "docker", "kubernetes", "aws", "gcp", "azure", "sql", "mongodb",
    "opencv", "ros", "labview", "verilog", "vhdl", "fpga", "solidworks",
    "autocad", "catia",
}

METHOD_KEYWORDS = {
    "cnn", "lstm", "rnn", "gnn", "transformer", "bert", "gpt", "gan",
    "reinforcement learning", "deep learning", "machine learning",
    "neural network", "random forest", "svm", "pca", "fem", "fea",
    "cfd", "dft", "monte carlo", "bayesian", "optimization",
    "nlp", "computer vision", "transfer learning", "attention mechanism",
    "autoencoder", "clustering", "regression", "classification",
    "signal processing", "control theory", "finite element",
    "molecular dynamics", "density functional",
}

DOMAIN_KEYWORDS = {
    "healthcare", "automotive", "robotics", "aerospace", "energy",
    "manufacturing", "telecommunications", "agriculture", "finance",
    "education", "climate", "environment", "biomedical", "pharmaceutical",
    "materials science", "nanotechnology", "semiconductor", "iot",
    "cybersecurity", "smart grid", "renewable energy", "sustainability",
    "drug discovery", "genomics", "proteomics",
}


def extract_ner_skills(text: str) -> dict:
    """Extract structured NER fields from publication/research text."""
    text_lower = text.lower()
    found_tech = [kw for kw in TECH_KEYWORDS if re.search(r'\b' + re.escape(kw) + r'\b', text_lower)]
    found_methods = [kw for kw in METHOD_KEYWORDS if kw in text_lower]
    found_domains = [kw for kw in DOMAIN_KEYWORDS if kw in text_lower]
    return {
        "technologies": sorted(set(found_tech)),
        "methods": sorted(set(found_methods)),
        "domains": sorted(set(found_domains)),
    }


class ProfileBuilder:
    """Builds enriched professor profiles by aggregating local + API data."""

    OPENALEX_BASE = "https://api.openalex.org"
    SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, timeout: int = 12):
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def _fetch_openalex(self, session: aiohttp.ClientSession, name: str) -> dict:
        """Search OpenAlex for author and their top works."""
        result = {
            "openalex_id": None,
            "total_publications": 0,
            "total_citations": 0,
            "h_index": 0,
            "top_papers": [],
        }
        try:
            # Search for author
            url = f"{self.OPENALEX_BASE}/authors"
            # IIT Madras OpenAlex institution ID: I24676775
            params = {
                "search": name,
                "filter": "affiliations.institution.id:I24676775",
                "select": "id,display_name,works_count,cited_by_count,summary_stats",
                "per_page": "3",
            }
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                else:
                    data = {"results": []}

            authors = data.get("results", [])
            if not authors:
                # Retry without institution filter, append IIT Madras to search
                params2 = {
                    "search": f"{name} IIT Madras",
                    "select": "id,display_name,works_count,cited_by_count,summary_stats",
                    "per_page": "3",
                }
                async with session.get(url, params=params2) as resp:
                    if resp.status != 200:
                        return result
                    data = await resp.json()
                authors = data.get("results", [])
                if not authors:
                    return result

            author = authors[0]
            result["openalex_id"] = author.get("id", "").replace("https://openalex.org/", "")
            result["total_publications"] = author.get("works_count", 0)
            result["total_citations"] = author.get("cited_by_count", 0)
            stats = author.get("summary_stats", {})
            result["h_index"] = stats.get("h_index", 0)

            # Get top works
            author_id = author.get("id", "").replace("https://openalex.org/", "")
            if author_id:
                works_url = f"{self.OPENALEX_BASE}/works"
                works_params = {
                    "filter": f"authorships.author.id:{author_id},type:article",
                    "sort": "cited_by_count:desc",
                    "per_page": "5",
                    "select": "title,publication_year,cited_by_count,primary_location,doi",
                }
                async with session.get(works_url, params=works_params) as resp:
                    if resp.status == 200:
                        works_data = await resp.json()
                        for w in works_data.get("results", []):
                            loc = w.get("primary_location", {}) or {}
                            source = loc.get("source", {}) or {}
                            result["top_papers"].append({
                                "title": w.get("title", ""),
                                "year": w.get("publication_year"),
                                "journal": source.get("display_name", ""),
                                "citations": w.get("cited_by_count", 0),
                                "doi": w.get("doi", ""),
                            })
        except Exception:
            pass
        return result

    async def _fetch_semantic_scholar(self, session: aiohttp.ClientSession, name: str) -> dict:
        """Search Semantic Scholar for author info as backup."""
        result = {
            "semantic_scholar_id": None,
            "ss_paper_count": 0,
            "ss_citation_count": 0,
            "ss_h_index": 0,
        }
        try:
            url = f"{self.SEMANTIC_SCHOLAR_BASE}/author/search"
            params = {
                "query": name,
                "fields": "name,affiliations,paperCount,citationCount,hIndex",
                "limit": "5",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return result
                data = await resp.json()

            authors = data.get("data", [])
            if not authors:
                return result

            # Find best match (prefer IIT Madras affiliation)
            best = authors[0]
            for a in authors:
                affiliations = " ".join(a.get("affiliations", []) or []).lower()
                if "iit madras" in affiliations or "indian institute of technology madras" in affiliations:
                    best = a
                    break

            result["semantic_scholar_id"] = best.get("authorId")
            result["ss_paper_count"] = best.get("paperCount", 0)
            result["ss_citation_count"] = best.get("citationCount", 0)
            result["ss_h_index"] = best.get("hIndex", 0)
        except Exception:
            pass
        return result

    # ── SOURCE 3: ORCID ──────────────────────────────────────────────────

    async def _fetch_orcid(self, session: aiohttp.ClientSession, name: str) -> dict:
        """Search ORCID for author and fetch their record."""
        result = {
            "orcid_id": None,
            "orcid_verified": False,
            "orcid_education": [],
            "orcid_employment": [],
            "orcid_works_count": 0,
        }
        try:
            # Split name into parts for structured search
            parts = name.strip().split()
            if len(parts) < 2:
                return result
            # Heuristic: last token is family name, rest is given
            non_initials = [p.replace(".", "") for p in parts if len(p.replace(".", "")) > 2]
            if len(non_initials) >= 2:
                given = non_initials[0]
                family = non_initials[-1]
            elif len(non_initials) == 1:
                family = non_initials[0]
                given = parts[0].replace(".", "")
            else:
                family = parts[-1].replace(".", "")
                given = parts[0].replace(".", "")

            search_url = "https://pub.orcid.org/v3.0/search"
            headers = {"Accept": "application/json"}
            # ORCID uses Lucene syntax — AND operator, quoted institution name
            inst = '"Indian Institute of Technology Madras"'
            query = f'family-name:{family} AND given-names:{given} AND affiliation-org-name:{inst}'

            async with session.get(
                search_url, params={"q": query}, headers=headers
            ) as resp:
                if resp.status != 200:
                    return result
                data = await resp.json()

            results_list = data.get("result", [])
            if not results_list:
                # Retry with just family name + institution
                query2 = f'family-name:{family} AND affiliation-org-name:{inst}'
                async with session.get(
                    search_url, params={"q": query2}, headers=headers
                ) as resp:
                    if resp.status != 200:
                        return result
                    data = await resp.json()
                results_list = data.get("result", [])
                if not results_list:
                    return result

            # Take first result's ORCID ID
            orcid_rec = results_list[0].get("orcid-identifier", {})
            orcid_id = orcid_rec.get("path", "")
            if not orcid_id:
                return result

            result["orcid_id"] = orcid_id

            # Fetch full record
            record_url = f"https://pub.orcid.org/v3.0/{orcid_id}/record"
            async with session.get(record_url, headers=headers) as resp:
                if resp.status != 200:
                    return result
                record = await resp.json()

            # Extract verified email
            emails_data = record.get("person", {}).get("emails", {}).get("email", [])
            if emails_data:
                result["orcid_verified"] = any(
                    e.get("verified", False) for e in emails_data
                )

            # Extract education
            edu_group = (
                record.get("activities-summary", {})
                .get("educations", {})
                .get("affiliation-group", [])
            )
            for group in edu_group:
                for summary in group.get("summaries", []):
                    es = summary.get("education-summary", {})
                    org = es.get("organization", {}) or {}
                    role = es.get("role-title", "") or ""
                    dept = es.get("department-name", "") or ""
                    org_name = org.get("name", "") or ""
                    entry = f"{role}, {dept}, {org_name}".strip(", ")
                    if entry:
                        result["orcid_education"].append(entry)

            # Extract employment
            emp_group = (
                record.get("activities-summary", {})
                .get("employments", {})
                .get("affiliation-group", [])
            )
            for group in emp_group:
                for summary in group.get("summaries", []):
                    es = summary.get("employment-summary", {})
                    org = es.get("organization", {}) or {}
                    role = es.get("role-title", "") or ""
                    org_name = org.get("name", "") or ""
                    entry = f"{role}, {org_name}".strip(", ")
                    if entry:
                        result["orcid_employment"].append(entry)

            # Works count
            works_summary = (
                record.get("activities-summary", {}).get("works", {})
            )
            work_groups = works_summary.get("group", [])
            result["orcid_works_count"] = len(work_groups)

        except Exception:
            pass
        return result

    # ── SOURCE 4: CrossRef ───────────────────────────────────────────────

    async def _fetch_crossref(self, session: aiohttp.ClientSession, name: str) -> dict:
        """Search CrossRef for papers by author affiliated with IIT Madras."""
        result = {
            "crossref_papers": [],
        }
        try:
            url = "https://api.crossref.org/works"
            params = {
                "query.author": name,
                "query.affiliation": "IIT Madras",
                "rows": "5",
                "sort": "is-referenced-by-count",
                "order": "desc",
                "select": "title,container-title,published,DOI,is-referenced-by-count,publisher",
            }
            headers = {
                "User-Agent": "CollabV/1.0 (mailto:ai@collabv.in)",
            }
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status != 200:
                    return result
                data = await resp.json()

            items = data.get("message", {}).get("items", [])
            for item in items:
                title_list = item.get("title", [])
                title = title_list[0] if title_list else ""
                container = item.get("container-title", [])
                journal = container[0] if container else ""
                # Parse year from published date-parts
                published = item.get("published", {})
                date_parts = published.get("date-parts", [[]])
                year = date_parts[0][0] if date_parts and date_parts[0] else None
                result["crossref_papers"].append({
                    "title": title,
                    "journal": journal,
                    "year": year,
                    "doi": item.get("DOI", ""),
                    "citations": item.get("is-referenced-by-count", 0),
                    "publisher": item.get("publisher", ""),
                    "source": "crossref",
                })
        except Exception:
            pass
        return result

    # ── SOURCE 5: arXiv ──────────────────────────────────────────────────

    async def _fetch_arxiv(self, session: aiohttp.ClientSession, name: str) -> dict:
        """Search arXiv for preprints by author."""
        result = {
            "arxiv_preprints": [],
        }
        try:
            # Build author query — arXiv uses au:Lastname_Firstname format
            parts = name.strip().split()
            non_initials = [p.replace(".", "") for p in parts if len(p.replace(".", "")) > 2]
            if len(non_initials) >= 2:
                arxiv_author = f"{non_initials[-1]}_{non_initials[0]}"
            elif non_initials:
                arxiv_author = non_initials[0]
            else:
                arxiv_author = parts[-1].replace(".", "")

            url = "http://export.arxiv.org/api/query"
            params = {
                "search_query": f"au:{arxiv_author}",
                "max_results": "5",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return result
                xml_text = await resp.text()

            # Parse Atom XML
            ns = {
                "atom": "http://www.w3.org/2005/Atom",
                "arxiv": "http://arxiv.org/schemas/atom",
            }
            root = ET.fromstring(xml_text)
            entries = root.findall("atom:entry", ns)

            for entry in entries:
                title_el = entry.find("atom:title", ns)
                title = title_el.text.strip().replace("\n", " ") if title_el is not None else ""
                summary_el = entry.find("atom:summary", ns)
                abstract = ""
                if summary_el is not None and summary_el.text:
                    abstract = summary_el.text.strip().replace("\n", " ")[:200]
                published_el = entry.find("atom:published", ns)
                submitted = published_el.text[:10] if published_el is not None else ""
                # arXiv ID from the id element
                id_el = entry.find("atom:id", ns)
                arxiv_id = ""
                if id_el is not None and id_el.text:
                    arxiv_id = id_el.text.replace("http://arxiv.org/abs/", "")
                # Categories
                categories = []
                for cat in entry.findall("atom:category", ns):
                    term = cat.get("term", "")
                    if term:
                        categories.append(term)
                # Also check arxiv:primary_category
                pcat = entry.find("arxiv:primary_category", ns)
                if pcat is not None:
                    pt = pcat.get("term", "")
                    if pt and pt not in categories:
                        categories.insert(0, pt)

                result["arxiv_preprints"].append({
                    "title": title,
                    "submitted_date": submitted,
                    "arxiv_id": arxiv_id,
                    "abstract": abstract,
                    "categories": categories[:5],
                    "source": "arxiv",
                })
        except Exception:
            pass
        return result

    # ── MAIN PROFILE BUILDER ─────────────────────────────────────────────

    async def build_profile_async(self, professor: dict) -> dict:
        """
        Build a unified enriched profile.
        Step 1: Local data (instant)
        Step 2: OpenAlex + Semantic Scholar + ORCID + CrossRef + arXiv (async, parallel)
        Step 3: NER extraction
        Step 4: Assemble unified profile
        """
        name = professor.get("name", "")
        sources_used = ["local_db"]

        # Step 1: Local data
        research_areas = professor.get("Research Interests", [])
        if isinstance(research_areas, str):
            research_areas = [research_areas]
        expertise_tags = professor.get("Areas of expertise", [])
        if isinstance(expertise_tags, str):
            expertise_tags = [expertise_tags]
        publications_local = professor.get("Most recently published papers or publications", [])
        if isinstance(publications_local, str):
            publications_local = [publications_local]
        education = professor.get("Education", [])
        if isinstance(education, str):
            education = [education]

        # Step 2: Fetch from ALL 5 APIs in parallel
        openalex_data = {"openalex_id": None, "total_publications": 0, "total_citations": 0, "h_index": 0, "top_papers": []}
        ss_data = {"semantic_scholar_id": None, "ss_paper_count": 0, "ss_citation_count": 0, "ss_h_index": 0}
        orcid_data = {"orcid_id": None, "orcid_verified": False, "orcid_education": [], "orcid_employment": [], "orcid_works_count": 0}
        crossref_data = {"crossref_papers": []}
        arxiv_data = {"arxiv_preprints": []}

        try:
            # arXiv redirects HTTP→HTTPS but has cert issues on some systems
            arxiv_ssl = ssl.create_default_context()
            arxiv_ssl.check_hostname = False
            arxiv_ssl.verify_mode = ssl.CERT_NONE
            arxiv_conn = aiohttp.TCPConnector(ssl=arxiv_ssl)
            async with aiohttp.ClientSession(timeout=self.timeout) as session, \
                       aiohttp.ClientSession(timeout=self.timeout, connector=arxiv_conn) as arxiv_session:
                oa_task = asyncio.create_task(self._fetch_openalex(session, name))
                ss_task = asyncio.create_task(self._fetch_semantic_scholar(session, name))
                orcid_task = asyncio.create_task(self._fetch_orcid(session, name))
                crossref_task = asyncio.create_task(self._fetch_crossref(session, name))
                arxiv_task = asyncio.create_task(self._fetch_arxiv(arxiv_session, name))
                openalex_data, ss_data, orcid_data, crossref_data, arxiv_data = (
                    await asyncio.gather(oa_task, ss_task, orcid_task, crossref_task, arxiv_task)
                )
                if openalex_data.get("openalex_id"):
                    sources_used.append("openalex")
                if ss_data.get("semantic_scholar_id"):
                    sources_used.append("semantic_scholar")
                if orcid_data.get("orcid_id"):
                    sources_used.append("orcid")
                if crossref_data.get("crossref_papers"):
                    sources_used.append("crossref")
                if arxiv_data.get("arxiv_preprints"):
                    sources_used.append("arxiv")
        except Exception:
            pass

        # Step 4: NER extraction
        all_text = " ".join(research_areas + expertise_tags + publications_local)
        ner_results = extract_ner_skills(all_text)

        # Use best available metrics (prefer OpenAlex, fallback to Semantic Scholar)
        total_pubs = openalex_data["total_publications"] or ss_data["ss_paper_count"] or len(publications_local)
        total_cites = openalex_data["total_citations"] or ss_data["ss_citation_count"] or 0
        h_index = openalex_data["h_index"] or ss_data["ss_h_index"] or 0

        # Top papers: prefer OpenAlex, else format local
        recent_papers = openalex_data.get("top_papers", [])
        if not recent_papers:
            for pub in publications_local[:5]:
                # Try to parse year from publication string
                year_match = re.search(r'(\d{4})', str(pub))
                recent_papers.append({
                    "title": str(pub),
                    "year": int(year_match.group(1)) if year_match else None,
                    "journal": "",
                    "citations": 0,
                    "doi": "",
                })

        # Compute derived scores
        designation = professor.get("designation", "")
        seniority = "Mid"
        desig_lower = designation.lower()
        if "assistant" in desig_lower:
            seniority = "Junior"
        elif "associate" in desig_lower:
            seniority = "Mid"
        elif "professor" in desig_lower:
            seniority = "Senior"

        # Industry fit scores based on research areas
        industry_fit = self._compute_industry_fit(research_areas, expertise_tags)
        domain_scores = self._compute_domain_scores(research_areas, expertise_tags)

        # Collaboration readiness score
        collab_score = min(1.0, (
            (0.3 if total_pubs > 50 else 0.15) +
            (0.2 if total_cites > 500 else 0.1) +
            (0.2 if h_index > 10 else 0.1) +
            (0.15 if len(research_areas) >= 3 else 0.05) +
            (0.15 if professor.get("Email Address") else 0.0)
        ))

        # Merge education from ORCID if richer than local
        if orcid_data.get("orcid_education") and len(orcid_data["orcid_education"]) > len(education):
            education = orcid_data["orcid_education"]

        # Confidence score based on data completeness
        confidence = 0.3  # base from local
        if "openalex" in sources_used:
            confidence += 0.4
        if "semantic_scholar" in sources_used:
            confidence += 0.3
        # Each extra source adds 0.05
        for extra_src in ("orcid", "crossref", "arxiv"):
            if extra_src in sources_used:
                confidence += 0.05
        confidence = min(1.0, confidence)

        # Step 4: Build unified profile
        profile = {
            "name": name,
            "designation": designation,
            "department": professor.get("department", ""),
            "university": "IIT Madras",
            "location": "Chennai, Tamil Nadu, India",
            "email": professor.get("Email Address", ""),
            "phone": professor.get("Phone Number", ""),
            "profile_url": professor.get("profile_url", ""),
            "research_areas": research_areas,
            "expertise_tags": expertise_tags,
            "ner_extracted_skills": (
                ner_results["technologies"] +
                ner_results["methods"] +
                ner_results["domains"]
            ),
            "total_publications": total_pubs,
            "total_citations": total_cites,
            "h_index": h_index,
            "recent_papers": recent_papers[:5],
            "academic_profiles": {
                "openalex_id": openalex_data.get("openalex_id"),
                "semantic_scholar_id": ss_data.get("semantic_scholar_id"),
                "orcid_id": orcid_data.get("orcid_id"),
                "orcid_verified": orcid_data.get("orcid_verified", False),
                "crossref_papers": crossref_data.get("crossref_papers", []),
                "arxiv_preprints": arxiv_data.get("arxiv_preprints", []),
                "profile_url": professor.get("profile_url", ""),
            },
            "orcid_employment": orcid_data.get("orcid_employment", []),
            "industry_fit": industry_fit,
            "domain_scores": domain_scores,
            "collaboration_readiness_score": round(collab_score, 2),
            "seniority_level": seniority,
            "experience_years": self._estimate_experience(designation, education),
            "education": education,
            "sources_used": sources_used,
            "confidence_score": round(confidence, 2),
            "last_updated": datetime.utcnow().isoformat() + "Z",
        }
        return profile

    def build_profile(self, professor: dict) -> dict:
        """Synchronous wrapper for build_profile_async."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    return pool.submit(
                        asyncio.run, self.build_profile_async(professor)
                    ).result()
        except RuntimeError:
            pass
        return asyncio.run(self.build_profile_async(professor))

    def _compute_industry_fit(self, research_areas: list, expertise: list) -> dict:
        """Compute industry fit scores based on keywords."""
        text = " ".join(research_areas + expertise).lower()
        industries = {
            "Technology": ["machine learning", "deep learning", "ai", "software", "nlp", "computer", "data"],
            "Healthcare": ["biomedical", "health", "medical", "drug", "clinical", "pharma"],
            "Automotive": ["automotive", "vehicle", "combustion", "engine", "electric vehicle"],
            "Aerospace": ["aerospace", "flight", "aerodynamic", "propulsion", "satellite"],
            "Energy": ["energy", "solar", "wind", "renewable", "power", "grid", "battery"],
            "Manufacturing": ["manufacturing", "material", "metal", "composite", "process"],
            "Finance": ["finance", "economics", "optimization", "risk", "quantitative"],
            "Telecom": ["wireless", "communication", "signal", "network", "antenna", "5g"],
        }
        scores = {}
        for industry, keywords in industries.items():
            count = sum(1 for kw in keywords if kw in text)
            scores[industry] = round(min(1.0, count / 3), 2)
        # Return only non-zero
        return {k: v for k, v in scores.items() if v > 0}

    def _compute_domain_scores(self, research_areas: list, expertise: list) -> dict:
        """Compute academic domain strength scores."""
        text = " ".join(research_areas + expertise).lower()
        domains = {
            "AI/ML": ["machine learning", "deep learning", "artificial intelligence", "neural", "nlp"],
            "Theory": ["theory", "mathematical", "analysis", "optimization", "algorithm"],
            "Systems": ["systems", "embedded", "vlsi", "hardware", "architecture"],
            "Applied": ["applied", "experimental", "simulation", "computational"],
            "Interdisciplinary": ["bio", "nano", "quantum", "climate", "social"],
        }
        scores = {}
        for domain, keywords in domains.items():
            count = sum(1 for kw in keywords if kw in text)
            scores[domain] = round(min(1.0, count / 2), 2)
        return {k: v for k, v in scores.items() if v > 0}

    def _estimate_experience(self, designation: str, education: list) -> str:
        """Estimate experience level from designation."""
        desig = designation.lower()
        if "assistant" in desig:
            return "5-15 years"
        elif "associate" in desig:
            return "10-20 years"
        elif "professor" in desig:
            return "20+ years"
        return "Unknown"
