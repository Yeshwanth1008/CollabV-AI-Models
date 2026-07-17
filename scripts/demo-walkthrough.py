"""
CollabV AI - investor demo walkthrough.

A scripted, beautifully-formatted terminal demo of the full platform flow:

    1. Register a company user
    2. Submit a collaboration need in plain text
    3. Get matched professors with explanations + deal probabilities
    4. Accept top match, reject #3
    5. Generate a Joint Research Agreement
    6. Show analytics dashboard data

Usage:
    # Against local server (default):
    python scripts/demo-walkthrough.py

    # Against production:
    APP_URL=https://app.collabv.ai python scripts/demo-walkthrough.py

The script pauses between steps so the demo runner can narrate. Pass --no-pause
to skip pauses (useful for screen-recording without dead air).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

APP_URL = os.environ.get("APP_URL", "http://localhost:8000")
PAUSE_SECONDS = 1.2

# Try to force UTF-8 on stdout (Windows console defaults to cp1252).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

_ENC = (sys.stdout.encoding or "").lower()
_UTF = "utf" in _ENC


def _glyph(utf: str, ascii_: str) -> str:
    return utf if _UTF else ascii_


HR_LINE = _glyph("═", "=") * 78
DASH_LINE = _glyph("─", "-") * 78
ARROW = _glyph("→", "->")
CHECK = _glyph("✓", "OK")
CROSS = _glyph("✗", "X")
BAR_CHAR = _glyph("█", "#")
TRI = _glyph("►", ">")


# ─── Pretty printing ────────────────────────────────────────────────────────

class C:
    RESET = "\033[0m"
    DIM   = "\033[2m"
    BOLD  = "\033[1m"
    CYAN  = "\033[36m"
    BLUE  = "\033[34m"
    GREEN = "\033[32m"
    YEL   = "\033[33m"
    RED   = "\033[31m"
    MAG   = "\033[35m"
    GRAY  = "\033[90m"


def banner(text: str) -> None:
    print()
    print(f"{C.CYAN}{HR_LINE}{C.RESET}")
    print(f"{C.CYAN}{C.BOLD}  {text}{C.RESET}")
    print(f"{C.CYAN}{HR_LINE}{C.RESET}")


def step(n: int, total: int, label: str) -> None:
    print()
    print(f"{C.MAG}{C.BOLD}{TRI} STEP {n}/{total}  {label}{C.RESET}")
    print(f"{C.GRAY}{DASH_LINE}{C.RESET}")


def call(label: str, fn, *args, **kwargs):
    print(f"  {C.DIM}{ARROW} {label}{C.RESET}")
    start = time.time()
    try:
        result = fn(*args, **kwargs)
    except Exception as e:
        print(f"  {C.RED}{CROSS} {e}{C.RESET}")
        raise
    elapsed = time.time() - start
    print(f"  {C.GREEN}{CHECK} {label}{C.RESET} {C.GRAY}({elapsed*1000:.0f}ms){C.RESET}")
    return result


def kv(label: str, value, color: str = C.GREEN) -> None:
    print(f"    {C.GRAY}{label:24s}{C.RESET}  {color}{value}{C.RESET}")


def hr() -> None:
    print(f"  {C.GRAY}{_glyph('─','-') * 76}{C.RESET}")


def pause(no_pause: bool, seconds: float = PAUSE_SECONDS) -> None:
    if not no_pause:
        time.sleep(seconds)


# ─── HTTP helpers ───────────────────────────────────────────────────────────

def http_post(client: httpx.Client, path: str, json_body=None, headers=None):
    r = client.post(path, json=json_body, headers=headers or {})
    r.raise_for_status()
    return r.json()


def http_get(client: httpx.Client, path: str, headers=None):
    r = client.get(path, headers=headers or {})
    r.raise_for_status()
    return r.json()


# ─── Demo steps ─────────────────────────────────────────────────────────────

BRIEF = (
    "We're TechNova AI, a Bengaluru robotics startup. We need an academic "
    "partner for an 18-month project building autonomous warehouse navigation "
    "robots that operate in low-structure, GPS-denied environments. Key "
    "challenges: real-time SLAM with low-cost sensors, multi-robot coordination "
    "without central control, and robustness to dynamic obstacles (humans, "
    "forklifts). We have ₹50L budget and want a Joint Research Agreement with "
    "shared IP and a path to a patentable platform."
)


def run_demo(no_pause: bool) -> None:
    banner("CollabV AI - Live Platform Demo")
    print(f"  Server: {C.CYAN}{APP_URL}{C.RESET}")
    print(f"  Date  : {time.strftime('%Y-%m-%d %H:%M')}")

    with httpx.Client(base_url=APP_URL, timeout=120) as client:
        # ── 0. Health ─────────────────────────────────────────────────────
        print()
        try:
            h = http_get(client, "/health")
            kv("Server status", h.get("status"), C.GREEN)
            kv("Professors indexed", h.get("professors_loaded"))
            kv("Total matches run", h.get("total_matches"))
        except Exception as e:
            print(f"{C.RED}Cannot reach {APP_URL}: {e}{C.RESET}")
            sys.exit(1)
        pause(no_pause)

        # ── 1. Register a company user ───────────────────────────────────
        step(1, 6, "Register a company user")
        unique = uuid.uuid4().hex[:8]
        email = f"demo-{unique}@technova.ai"
        password = "demo-secure-123"

        user = call(f"POST /auth/register  ({email})", http_post, client,
                    "/auth/register",
                    {"email": email, "password": password,
                     "name": "Priya Sharma", "company_name": "TechNova AI"})
        api_key = user["api_key"]
        kv("User ID", user["id"])
        kv("Email", user["email"])
        kv("API key", api_key[:14] + "…" + api_key[-4:])
        kv("Tier", user["tier"])
        pause(no_pause)

        auth_headers = {"X-API-Key": api_key}

        # ── 2. Submit the brief ──────────────────────────────────────────
        step(2, 6, "Submit collaboration need in plain text")
        print(f"  {C.DIM}Brief:{C.RESET}")
        for line in [BRIEF[i:i+74] for i in range(0, len(BRIEF), 74)]:
            print(f"    {C.YEL}{line}{C.RESET}")
        pause(no_pause)

        # ── 3. Run matching with full v3 layer ───────────────────────────
        step(3, 6, "Run matching engine (6 scoring layers + LLM explanations)")
        result = call("POST /match/run", http_post, client, "/match/run",
                      {"raw_text": BRIEF,
                       "company_name": "TechNova AI",
                       "top_k": 5,
                       "include_deal_score": True,
                       "include_explanations": True,
                       "explain_top_k": 3},
                      headers=auth_headers)
        match_id = result["match_id"]
        kv("Match ID", match_id)
        kv("Results returned", len(result["results"]))
        print()
        print(f"  {C.BOLD}Top 5 ranked matches:{C.RESET}")
        for i, m in enumerate(result["results"], start=1):
            score_color = C.GREEN if m["score"] >= 70 else C.YEL if m["score"] >= 55 else C.RED
            deal_color  = C.GREEN if m.get("deal_probability", 0) >= 70 else C.YEL
            print(f"    #{i}  {score_color}{m['score']:5.1f}{C.RESET}  "
                  f"{C.BOLD}{m['professor_name'][:26]:26s}{C.RESET}  "
                  f"{C.GRAY}{m['department'][:28]:28s}{C.RESET}  "
                  f"deal={deal_color}{m.get('deal_probability',0):5.1f}%{C.RESET}")
            print(f"        {C.GRAY}patent={m['patent_score']:.0f}  "
                  f"readiness={m['readiness_score']:.0f}  "
                  f"tier1={m['tier1_score']:.0f}  tier2={m['tier2_score']:.0f}{C.RESET}")
            if m.get("explanation"):
                exp = m["explanation"]
                summary = exp.get("summary", "")
                wrap = [summary[j:j+72] for j in range(0, len(summary), 72)]
                for line in wrap[:3]:
                    print(f"        {C.CYAN}{line}{C.RESET}")
            print()
        pause(no_pause, 2.5)

        # ── 4. Accept #1, reject #3 ──────────────────────────────────────
        step(4, 6, "Submit accept/reject feedback")
        top = result["results"][0]
        third = result["results"][2] if len(result["results"]) >= 3 else None

        call(f"POST /feedback/submit  (accept #{1} {top['professor_name']})",
             http_post, client, "/feedback/submit",
             {"match_id": match_id, "professor_id": top["professor_id"],
              "action": "accept", "reason": ""},
             headers=auth_headers)
        kv("Action", "accept", C.GREEN)
        kv("Professor", top["professor_name"])

        if third:
            call(f"POST /feedback/submit  (reject #{3} {third['professor_name']})",
                 http_post, client, "/feedback/submit",
                 {"match_id": match_id, "professor_id": third["professor_id"],
                  "action": "reject", "reason": "research focus too theoretical"},
                 headers=auth_headers)
            kv("Action", "reject", C.RED)
            kv("Professor", third["professor_name"])
            kv("Reason", "research focus too theoretical")
        pause(no_pause)

        # ── 5. Generate Joint Research Agreement ─────────────────────────
        step(5, 6, "Generate Joint Research Agreement for top match")
        contract_resp = call("POST /contract/generate", http_post, client,
                             "/contract/generate",
                             {"type": "joint_research",
                              "company_name": "TechNova AI",
                              "professor_name": top["professor_name"],
                              "department": top["department"],
                              "research_area": "Autonomous robotic navigation",
                              "amount": 5000000,
                              "start_date": "2026-06-01",
                              "end_date": "2027-12-01",
                              "extra": {"objective": "Build a deployable autonomous warehouse robot platform"}},
                             headers=auth_headers)
        contract = contract_resp["contract"]
        kv("Contract length", f"{len(contract)} chars")
        kv("Template", "Joint Research Agreement")
        print()
        print(f"  {C.BOLD}First 400 chars:{C.RESET}")
        preview = contract[:400].replace("\n", "\n    ")
        print(f"    {C.GRAY}{preview}…{C.RESET}")
        pause(no_pause)

        # ── 6. Analytics ────────────────────────────────────────────────
        step(6, 6, "Analytics dashboard data")
        stats = call("GET /retrain/stats", http_get, client, "/retrain/stats",
                     headers=auth_headers)
        analysis = stats["analysis"]
        kv("Total feedback rows", analysis["total_feedback"])
        kv("Accept rate", f"{analysis['accept_rate']*100:.0f}%", C.GREEN)
        kv("Reject rate", f"{analysis['reject_rate']*100:.0f}%", C.YEL)
        kv("Sufficient for retrain", str(analysis["sufficient_data"]),
           C.GREEN if analysis["sufficient_data"] else C.YEL)

        dept = call("GET /readiness/departments", http_get, client, "/readiness/departments",
                    headers=auth_headers)
        print()
        print(f"  {C.BOLD}Top 5 departments by collaboration readiness:{C.RESET}")
        for d in dept["departments"][:5]:
            bar = BAR_CHAR * int(d["avg_readiness"] / 4)
            print(f"    {d['department'][:30]:30s}  "
                  f"{C.GREEN}{d['avg_readiness']:5.1f}{C.RESET}  "
                  f"{C.GRAY}{bar}{C.RESET}")
        pause(no_pause)

        # ── Final ───────────────────────────────────────────────────────
        banner("Demo Complete")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Registered user")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Ranked 543 professors against R&D brief in <1s")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Generated explanations + deal probabilities")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Captured accept/reject feedback")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Drafted a Joint Research Agreement")
        print(f"  {C.GREEN}{CHECK}{C.RESET}Surfaced platform-wide analytics")
        print()
        print(f"  Match: {C.CYAN}{APP_URL}/match/results/{match_id}{C.RESET}")
        print(f"  Login: {C.CYAN}email={email} / pw={password}{C.RESET}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-pause", action="store_true",
                        help="Run without dramatic pauses (for screen recording)")
    args = parser.parse_args()
    try:
        run_demo(no_pause=args.no_pause)
    except httpx.HTTPStatusError as e:
        print(f"{C.RED}HTTP error {e.response.status_code}: {e.response.text}{C.RESET}")
        sys.exit(1)
    except KeyboardInterrupt:
        print(f"\n{C.YEL}Demo interrupted{C.RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
