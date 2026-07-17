"""
Test all 5 data sources and show enriched profiles for 2 professors.
"""
import asyncio
import json
import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from retrieval.search_engine import HybridSearchEngine
from retrieval.profile_builder import ProfileBuilder
from retrieval.cache import ProfileCache

SEP = "=" * 65


def print_profile(profile):
    """Pretty-print an enriched profile."""
    print(f"  Name:            {profile['name']}")
    print(f"  Designation:     {profile['designation']}")
    print(f"  Department:      {profile['department']}")
    print(f"  Email:           {profile['email']}")
    print(f"  Seniority:       {profile['seniority_level']} ({profile['experience_years']})")
    print()
    print(f"  Publications:    {profile['total_publications']}")
    print(f"  Citations:       {profile['total_citations']}")
    print(f"  H-Index:         {profile['h_index']}")
    print(f"  Collab Score:    {profile['collaboration_readiness_score']}")
    print()

    # Sources
    print(f"  Sources Used:    {', '.join(profile['sources_used'])}")
    print(f"  Confidence:      {profile['confidence_score']}")
    print()

    # Academic profiles
    ap = profile.get("academic_profiles", {})
    print(f"  OpenAlex ID:     {ap.get('openalex_id', 'N/A')}")
    print(f"  Sem. Scholar ID: {ap.get('semantic_scholar_id', 'N/A')}")
    print(f"  ORCID ID:        {ap.get('orcid_id', 'N/A')}")
    print(f"  ORCID Verified:  {ap.get('orcid_verified', False)}")
    print()

    # Research areas
    print(f"  Research Areas:  {', '.join(profile.get('research_areas', [])[:5])}")
    skills = profile.get("ner_extracted_skills", [])
    if skills:
        print(f"  NER Skills:      {', '.join(skills[:8])}")
    print()

    # ORCID employment
    emp = profile.get("orcid_employment", [])
    if emp:
        print(f"  ORCID Employment ({len(emp)}):")
        for e in emp[:3]:
            print(f"    - {e}")
    print()

    # Education
    edu = profile.get("education", [])
    if edu:
        print(f"  Education ({len(edu)}):")
        for e in edu[:4]:
            print(f"    - {e}")
    print()

    # Top papers (from OpenAlex)
    papers = profile.get("recent_papers", [])
    if papers:
        print(f"  Top Papers ({len(papers)}):")
        for p in papers[:3]:
            title = (p.get("title") or "Untitled")[:75]
            yr = p.get("year", "?")
            cites = p.get("citations", 0)
            journal = p.get("journal", "")
            print(f"    [{yr}] {title}")
            if journal:
                print(f"         {journal} | {cites} citations")
    print()

    # CrossRef papers
    cr_papers = ap.get("crossref_papers", [])
    if cr_papers:
        print(f"  CrossRef Papers ({len(cr_papers)}):")
        for p in cr_papers[:3]:
            title = (p.get("title") or "Untitled")[:75]
            yr = p.get("year", "?")
            cites = p.get("citations", 0)
            pub = p.get("publisher", "")
            print(f"    [{yr}] {title}")
            print(f"         {p.get('journal', '')} | {cites} citations | {pub}")
    print()

    # arXiv preprints
    arxiv = ap.get("arxiv_preprints", [])
    if arxiv:
        print(f"  arXiv Preprints ({len(arxiv)}):")
        for p in arxiv[:3]:
            title = (p.get("title") or "Untitled")[:75]
            date = p.get("submitted_date", "?")
            cats = ", ".join(p.get("categories", [])[:3])
            print(f"    [{date}] {title}")
            print(f"         ID: {p.get('arxiv_id', '')} | Categories: {cats}")
            if p.get("abstract"):
                print(f"         Abstract: {p['abstract'][:120]}...")
    print()

    # Industry fit
    ind = profile.get("industry_fit", {})
    if ind:
        top = sorted(ind.items(), key=lambda x: x[1], reverse=True)[:5]
        print(f"  Industry Fit:    {', '.join(f'{k}({int(v*100)}%)' for k, v in top)}")
    print()


async def test_professor(engine, builder, cache, query_name, test_label):
    """Search and build enriched profile for one professor."""
    print(f"\n{SEP}")
    print(f"  {test_label}")
    print(SEP)

    # Search
    start = time.perf_counter()
    results = engine.search(query=query_name, top_k=3)
    search_ms = (time.perf_counter() - start) * 1000
    print(f"\n  Search: '{query_name}' -> {len(results['results'])} results in {search_ms:.1f}ms")

    if not results["results"]:
        print("  NO RESULTS FOUND")
        return

    top = results["results"][0]
    print(f"  Top match: {top['name']} (confidence: {top['confidence']*100:.1f}%)")

    # Clear this prof from cache to force fresh API fetch
    cache_key = top["name"].lower().strip()
    import sqlite3
    conn = sqlite3.connect(str(cache.db_path))
    conn.execute("DELETE FROM profile_cache WHERE name_key = ?", (cache_key,))
    conn.commit()
    conn.close()

    # Build enriched profile (fresh from all APIs)
    prof_data = engine.get_professor(top["index"])
    start = time.perf_counter()
    profile = await builder.build_profile_async(prof_data)
    build_ms = (time.perf_counter() - start) * 1000
    print(f"  Profile built in {build_ms:.0f}ms from {len(profile['sources_used'])} sources\n")

    print_profile(profile)

    # Save to cache
    cache.save(top["name"], profile)

    return profile


async def main():
    print(SEP)
    print("  5-Source Faculty Profile Enrichment Test")
    print("  Sources: Local DB + OpenAlex + Semantic Scholar + ORCID + CrossRef + arXiv")
    print(SEP)

    engine = HybridSearchEngine()
    engine.load_indexes()
    builder = ProfileBuilder(timeout=12)
    cache = ProfileCache()

    print(f"\n  Professors indexed: {len(engine.professors)}")

    # Test 1: Mitesh Khapra (CS — should have arXiv preprints)
    p1 = await test_professor(
        engine, builder, cache,
        "Mitesh Khapra",
        "TEST 1: Mitesh Khapra (CS/AI — expect arXiv preprints)"
    )

    # Small delay to avoid rate limits
    await asyncio.sleep(1)

    # Test 2: Pradeep T (Chemistry — should have CrossRef papers)
    p2 = await test_professor(
        engine, builder, cache,
        "Pradeep T",
        "TEST 2: Pradeep T (Chemistry — expect CrossRef papers)"
    )

    await asyncio.sleep(1)

    # Test 3: Sujith R.I (Aerospace — well-known, likely has ORCID)
    p3 = await test_professor(
        engine, builder, cache,
        "Sujith",
        "TEST 3: Sujith R.I (Aerospace — expect ORCID)"
    )

    # Summary
    print(f"\n{SEP}")
    print("  SUMMARY")
    print(SEP)
    for label, p in [("Mitesh Khapra", p1), ("Pradeep T", p2), ("Sujith R.I", p3)]:
        if p:
            srcs = ", ".join(p["sources_used"])
            ap = p.get("academic_profiles", {})
            print(f"\n  {label}:")
            print(f"    Sources:    {srcs}")
            print(f"    Confidence: {p['confidence_score']}")
            print(f"    OpenAlex:   {'Yes' if ap.get('openalex_id') else 'No'}")
            print(f"    Sem.Schol:  {'Yes' if ap.get('semantic_scholar_id') else 'No'}")
            print(f"    ORCID:      {ap.get('orcid_id') or 'No'}")
            print(f"    CrossRef:   {len(ap.get('crossref_papers', []))} papers")
            print(f"    arXiv:      {len(ap.get('arxiv_preprints', []))} preprints")


if __name__ == "__main__":
    asyncio.run(main())
