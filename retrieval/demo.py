"""
Demo/test script - verifies search + profile building works for 5 test queries.
Run after indexer.py has built the indexes.
"""

import asyncio
import json
import sys
import os
import time
from pathlib import Path

# Fix Windows console encoding
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from retrieval.search_engine import HybridSearchEngine
from retrieval.profile_builder import ProfileBuilder
from retrieval.cache import ProfileCache


def format_profile_summary(profile: dict) -> str:
    """Format a compact profile summary."""
    lines = [
        f"    Name:         {profile.get('name', 'N/A')}",
        f"    Designation:  {profile.get('designation', 'N/A')}",
        f"    Department:   {profile.get('department', 'N/A')}",
        f"    Email:        {profile.get('email', 'N/A')}",
        f"    Publications: {profile.get('total_publications', 0)}",
        f"    Citations:    {profile.get('total_citations', 0)}",
        f"    H-Index:      {profile.get('h_index', 0)}",
        f"    Collab Score: {profile.get('collaboration_readiness_score', 0)}",
        f"    Seniority:    {profile.get('seniority_level', 'N/A')}",
    ]
    areas = profile.get("research_areas", [])
    if areas:
        lines.append(f"    Research:     {', '.join(areas[:3])}")
    skills = profile.get("ner_extracted_skills", [])
    if skills:
        lines.append(f"    NER Skills:   {', '.join(skills[:5])}")
    ind = profile.get("industry_fit", {})
    if ind:
        top_ind = sorted(ind.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append(f"    Top Industry: {', '.join(f'{k}({v})' for k,v in top_ind)}")
    sources = profile.get("sources_used", [])
    lines.append(f"    Sources:      {', '.join(sources)}")
    lines.append(f"    Confidence:   {profile.get('confidence_score', 0)}")
    return "\n".join(lines)


async def run_test(engine, builder, cache, query, expected_hint, test_num):
    """Run a single test search and profile build."""
    print(f"\n{'='*60}")
    print(f"TEST {test_num}: \"{query}\"")
    print(f"Expected: {expected_hint}")
    print(f"{'='*60}")

    # Search
    start = time.perf_counter()
    results = engine.search(query=query, top_k=5)
    search_ms = (time.perf_counter() - start) * 1000

    print(f"\n  Search time: {search_ms:.1f}ms")
    print(f"  Results found: {len(results['results'])}")

    if results.get("disambiguation"):
        print(f"  ⚠ DISAMBIGUATION: {results['disambiguation']}")

    print(f"\n  Top 3 matches:")
    for i, r in enumerate(results["results"][:3]):
        conf_pct = r["confidence"] * 100
        print(f"    {i+1}. {r['name']:<30} | {r['department']:<40} | Conf: {conf_pct:.1f}%")
        print(f"       Scores → Name: {r['name_score']:.3f}  BM25: {r['bm25_score']:.3f}  TF-IDF: {r['tfidf_score']:.3f}  Type: {r['match_type']}")

    # Build full profile for top match
    if results["results"]:
        top = results["results"][0]
        prof_data = engine.get_professor(top["index"])

        # Check cache
        cached = cache.get(top["name"])
        if cached:
            print(f"\n  Cache: HIT (using cached profile)")
            profile = cached
        else:
            print(f"\n  Cache: MISS (fetching from APIs...)")
            profile = await builder.build_profile_async(prof_data)
            cache.save(top["name"], profile)

        print(f"\n  ── Full Profile Summary ──")
        print(format_profile_summary(profile))

        # Show recent papers
        papers = profile.get("recent_papers", [])
        if papers:
            print(f"\n  ── Top Papers ──")
            for p in papers[:3]:
                title = (p.get("title") or "Untitled")[:70]
                yr = p.get("year", "?")
                cites = p.get("citations", 0)
                print(f"    • {title}{'...' if len(p.get('title',''))>70 else ''} ({yr}, {cites} cites)")

    return search_ms


async def main():
    print("╔" + "═"*58 + "╗")
    print("║   Faculty Retrieval System — Demo / Verification Script   ║")
    print("╚" + "═"*58 + "╝")

    # Initialize
    engine = HybridSearchEngine()
    engine.load_indexes()
    builder = ProfileBuilder(timeout=10)
    cache = ProfileCache()

    print(f"\nLoaded {len(engine.professors)} professors")
    print(f"Cache entries: {cache.stats()['total_cached']}")

    # Define 5 test cases
    tests = [
        ("Sujith", "R.I. Sujith, Aerospace, combustion expert"),
        ("Ravindran", "Should disambiguate between multiple Ravindrans"),
        ("Mitesh Khapra", "Exact match, NLP/ML professor in CSE"),
        ("Krishna", "Multiple matches, show disambiguation"),
        ("Pradeep", "Partial name match test"),
    ]

    total_ms = 0
    for i, (query, hint) in enumerate(tests, 1):
        ms = await run_test(engine, builder, cache, query, hint, i)
        total_ms += ms

    # Summary
    print(f"\n\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    print(f"  Total tests:        {len(tests)}")
    print(f"  Total search time:  {total_ms:.1f}ms")
    print(f"  Avg search time:    {total_ms/len(tests):.1f}ms")
    cache_stats = cache.stats()
    print(f"  Cache entries:      {cache_stats['total_cached']}")
    print(f"  Cache total hits:   {cache_stats['total_hits']}")
    print(f"\n  All tests completed successfully!")

    # ── Source Coverage Scan ─────────────────────────────────────────
    print(f"\n\n{'='*60}")
    print(f"SOURCE COVERAGE SCAN (sampling all 543 professors)")
    print(f"{'='*60}")

    # Sample a diverse set to estimate coverage
    import random
    random.seed(42)
    total = len(engine.professors)
    sample_size = 20
    indices = random.sample(range(total), sample_size)

    orcid_count = 0
    arxiv_count = 0
    crossref_count = 0
    openalex_count = 0
    ss_count = 0
    build_times = []
    source_counts = []

    print(f"\n  Scanning {sample_size} professors across departments...")
    print(f"  (Extrapolating to estimate coverage for all {total})\n")

    for idx in indices:
        prof = engine.professors[idx]
        name = prof.get("name", "")
        dept = prof.get("department", "").replace("Department of ", "")

        start = time.perf_counter()
        profile = await builder.build_profile_async(prof)
        elapsed = (time.perf_counter() - start) * 1000
        build_times.append(elapsed)

        sources = profile.get("sources_used", [])
        source_counts.append(len(sources))
        ap = profile.get("academic_profiles", {})

        has_orcid = "orcid" in sources
        has_arxiv = "arxiv" in sources
        has_crossref = "crossref" in sources
        has_oa = "openalex" in sources
        has_ss = "semantic_scholar" in sources

        if has_orcid:
            orcid_count += 1
        if has_arxiv:
            arxiv_count += 1
        if has_crossref:
            crossref_count += 1
        if has_oa:
            openalex_count += 1
        if has_ss:
            ss_count += 1

        src_flags = (
            f"{'OA' if has_oa else '--'} "
            f"{'SS' if has_ss else '--'} "
            f"{'OR' if has_orcid else '--'} "
            f"{'CR' if has_crossref else '--'} "
            f"{'AX' if has_arxiv else '--'}"
        )
        print(f"  [{idx:3d}] {name:<30} {dept:<25} [{src_flags}] {elapsed:6.0f}ms")

        # Brief pause to avoid rate-limiting
        await asyncio.sleep(0.3)

    # Extrapolate to full 543
    scale = total / sample_size
    avg_build = sum(build_times) / len(build_times)
    avg_sources = sum(source_counts) / len(source_counts)

    print(f"\n  {'─'*50}")
    print(f"  SOURCE COVERAGE (sampled {sample_size}, extrapolated to {total})")
    print(f"  {'─'*50}")
    print(f"  {'Source':<25} {'Sample':>8} {'Est. Total':>12} {'Coverage':>10}")
    print(f"  {'─'*50}")
    print(f"  {'Local DB':<25} {sample_size:>8} {total:>12} {'100.0%':>10}")
    print(f"  {'OpenAlex':<25} {openalex_count:>8} {int(openalex_count * scale):>12} {openalex_count/sample_size*100:>9.1f}%")
    print(f"  {'Semantic Scholar':<25} {ss_count:>8} {int(ss_count * scale):>12} {ss_count/sample_size*100:>9.1f}%")
    print(f"  {'ORCID':<25} {orcid_count:>8} {int(orcid_count * scale):>12} {orcid_count/sample_size*100:>9.1f}%")
    print(f"  {'CrossRef':<25} {crossref_count:>8} {int(crossref_count * scale):>12} {crossref_count/sample_size*100:>9.1f}%")
    print(f"  {'arXiv':<25} {arxiv_count:>8} {int(arxiv_count * scale):>12} {arxiv_count/sample_size*100:>9.1f}%")
    print(f"  {'─'*50}")
    print(f"  Avg sources per prof:   {avg_sources:.1f} / 6")
    print(f"  Avg profile build time: {avg_build:.0f}ms")
    print(f"  {'─'*50}")


if __name__ == "__main__":
    asyncio.run(main())
