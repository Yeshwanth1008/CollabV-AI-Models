#!/usr/bin/env bash
# Production smoke test - run after deploy.sh to verify the system is live.
#
# Exits non-zero on first failure. Prints PASS/FAIL per check.

set -uo pipefail

APP_URL="${APP_URL:-https://${DOMAIN_NAME:-app.yourdomain.com}}"
# Response-body scratch file. Use a RELATIVE path with no spaces so both
# git-bash redirects AND python's open() resolve to the same file. Absolute
# paths break on Windows when the repo dir contains spaces (curl mis-parses)
# or when git-bash's /c/... POSIX form doesn't resolve in Python.
RESP_FILE="${RESP_FILE:-./.smoke_resp.tmp}"
PASS=0
FAIL=0

green() { printf "  \033[32mPASS\033[0m  %s\n" "$*"; PASS=$((PASS+1)); }
red()   { printf "  \033[31mFAIL\033[0m  %s\n" "$*"; FAIL=$((FAIL+1)); }
header(){ printf "\n\033[36m== %s ==\033[0m\n" "$*"; }

curl_json() {
  curl -sS -o "${RESP_FILE}" -w "%{http_code}" "$@"
}

header "Basic health"
code=$(curl_json "$APP_URL/health")
if [ "$code" = "200" ] && grep -q '"status":"ok"' ${RESP_FILE}; then
  green "/health returns 200 + status:ok"
else
  red "/health returned $code"; cat ${RESP_FILE}
fi

code=$(curl_json "$APP_URL/health/deep")
if [ "$code" = "200" ]; then
  green "/health/deep returns 200"
  cat ${RESP_FILE} | python -m json.tool 2>/dev/null | head -30 || true
else
  red "/health/deep returned $code"
fi

header "TLS + headers"
hdrs=$(curl -sIL "$APP_URL/health")
echo "$hdrs" | grep -qi "strict-transport-security" \
  && green "HSTS header present" \
  || red "HSTS header missing"
echo "$hdrs" | grep -qi "x-frame-options: DENY" \
  && green "X-Frame-Options: DENY" \
  || red "X-Frame-Options missing"
echo "$hdrs" | grep -qi "x-content-type-options: nosniff" \
  && green "X-Content-Type-Options: nosniff" \
  || red "X-Content-Type-Options missing"

header "Auth - register + login"
EMAIL="smoketest-$(date +%s)@example.com"
PW="smoke12345"
code=$(curl_json -X POST "$APP_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\",\"name\":\"Smoke Test\"}")
if [ "$code" = "200" ]; then
  API_KEY=$(python -c "import json;print(json.load(open('${RESP_FILE}'))['api_key'])")
  green "Register -> 200, api_key issued"
else
  red "Register returned $code"; cat ${RESP_FILE}
  API_KEY=""
fi

code=$(curl_json -X POST "$APP_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}")
[ "$code" = "200" ] && green "Login -> 200" || red "Login returned $code"

# Invalid password
code=$(curl_json -X POST "$APP_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"wrong\"}")
[ "$code" = "401" ] && green "Wrong password -> 401" || red "Wrong password returned $code"

header "Match endpoint"
if [ -n "$API_KEY" ]; then
  code=$(curl_json -X POST "$APP_URL/match/run" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: $API_KEY" \
    -d '{"raw_text":"ML for autonomous vehicles","top_k":3,"include_deal_score":true,"include_explanations":true}')
  if [ "$code" = "200" ]; then
    green "/match/run with API key -> 200"
    # Verify v3 fields present in response
    python <<EOF
import json
data = json.load(open('${RESP_FILE}'))
required = ['patent_score','readiness_score','contextual_readiness']
top = data['results'][0]
missing = [f for f in required if f not in top]
if missing:
    print(f"  FAIL  missing fields: {missing}")
    exit(1)
print(f"  PASS  patent_score={top['patent_score']}, readiness_score={top['readiness_score']}")
if 'deal_assessment' in top:
    print(f"  PASS  deal_assessment present (probability={top.get('deal_probability')}%)")
else:
    print(f"  FAIL  deal_assessment missing from top match")
if 'explanation' in top:
    print(f"  PASS  explanation present (source={top['explanation']['source']})")
else:
    print(f"  FAIL  explanation missing from top match")
EOF
  else
    red "/match/run returned $code"; cat ${RESP_FILE}
  fi

  # Invalid auth check
  code=$(curl_json -X POST "$APP_URL/match/run" \
    -H "Content-Type: application/json" \
    -H "X-API-Key: invalid-key-12345" \
    -d '{"raw_text":"test","top_k":1}')
  [ "$code" = "401" ] && green "Invalid API key -> 401" || red "Invalid API key returned $code"
fi

header "Rate limiting"
if [ -n "$API_KEY" ]; then
  # Hit /match/run rapidly - should eventually 429
  hit429=0
  for i in {1..40}; do
    code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$APP_URL/match/run" \
      -H "Content-Type: application/json" -H "X-API-Key: $API_KEY" \
      -d '{"raw_text":"test","top_k":1,"include_deal_score":false,"include_explanations":false}')
    if [ "$code" = "429" ]; then hit429=1; break; fi
  done
  [ $hit429 -eq 1 ] && green "Rate limiter eventually returned 429" \
    || red "No 429 after 40 rapid requests (rate limit not enforced)"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Marketplace — the part this script gates CI on.
#
# Preconditions: the deployment is seeded with the two known dev accounts via
# scripts/seed_test_accounts.py:
#   admin@example.com    role=admin
#   inventor@example.com role=professor_user, linked_professor_id=IITM-0143
# Override via SMOKE_*_EMAIL / SMOKE_*_PASSWORD env vars for staging fixtures.
# ═══════════════════════════════════════════════════════════════════════════

ADMIN_EMAIL="${SMOKE_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${SMOKE_ADMIN_PASSWORD:-AdminTest!23}"
INVENTOR_EMAIL="${SMOKE_INVENTOR_EMAIL:-inventor@example.com}"
INVENTOR_PASSWORD="${SMOKE_INVENTOR_PASSWORD:-InventorTest!23}"
INVENTOR_PROF_ID="${SMOKE_INVENTOR_PROF_ID:-IITM-0143}"
STUB_PROF_ID="${SMOKE_STUB_PROF_ID:-STUB-RAMAPRABHU}"

# Run-scoped ephemeral identifiers so re-runs don't collide
RUN_TAG="$(date +%s)-$$"
FRESH_PROF_EMAIL="smoke-fresh-${RUN_TAG}@example.com"
STUB_CLAIMANT_EMAIL="smoke-stub-${RUN_TAG}@example.com"
EPHEMERAL_PW="SmokeTest!23"

# Track listings we toggled to active so we can rewind at cleanup
declare -a TOUCHED_LISTINGS=()

# State carried across sections
ADMIN_TOKEN=""
INVENTOR_TOKEN=""
ACTIVE_LISTING_ID=""
INQUIRY_ID=""

# ─── Helpers ─────────────────────────────────────────────────────────────
# `http` is an alias for the existing curl_json so the new code reads naturally;
# both write the body to ${RESP_FILE} and echo the HTTP status code.
http() { curl_json "$@"; }

mp_login() {
  # $1=email $2=password -> echoes access_token, returns 1 on failure
  local body
  body=$(curl -sS -X POST "$APP_URL/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"$1\",\"password\":\"$2\"}")
  echo "$body" | python -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null
}

jget() {
  # $1=jsonpath like 'foo.bar' or 'arr.0.field' on ${RESP_FILE}
  python -c "
import json,sys
d = json.load(open('${RESP_FILE}'))
for k in sys.argv[1].split('.'):
    d = d[int(k)] if isinstance(d, list) else d.get(k)
    if d is None: break
print('' if d is None else d)
" "$1" 2>/dev/null
}

assert_status() {
  # $1=actual $2=expected $3=label
  if [ "$1" = "$2" ]; then green "$3 (HTTP $1)"
  else red "$3 (expected HTTP $2, got $1; body: $(head -c 200 ${RESP_FILE}))"; fi
}

assert_eq() {
  # $1=actual $2=expected $3=label
  if [ "$1" = "$2" ]; then green "$3 (= $1)"
  else red "$3 (expected '$2', got '$1')"; fi
}

assert_ne() {
  if [ "$1" != "$2" ]; then green "$3 (= $1)"
  else red "$3 (expected != '$2', got '$1')"; fi
}

# ─── Section M1: Marketplace preflight ───────────────────────────────────
header "Marketplace — preflight"
ADMIN_TOKEN=$(mp_login "$ADMIN_EMAIL" "$ADMIN_PASSWORD")
[ -n "$ADMIN_TOKEN" ] && green "Admin login -> token" \
  || { red "Admin login FAILED — is $ADMIN_EMAIL seeded? Run scripts/seed_test_accounts.py."; }

INVENTOR_TOKEN=$(mp_login "$INVENTOR_EMAIL" "$INVENTOR_PASSWORD")
[ -n "$INVENTOR_TOKEN" ] && green "Inventor login -> token" \
  || { red "Inventor login FAILED — is $INVENTOR_EMAIL seeded?"; }

# Bail early if fixtures are missing — every subsequent check needs the tokens
if [ -z "$ADMIN_TOKEN" ] || [ -z "$INVENTOR_TOKEN" ]; then
  red "Fixtures missing — aborting marketplace section."
  printf "  \033[32m%d passed\033[0m, \033[31m%d failed\033[0m\n" "$PASS" "$FAIL"
  exit 1
fi

# ─── Section M2: Engine readiness (the new gate) ─────────────────────────
header "Marketplace — engine readiness"

# 2a. /marketplace/status reports degraded=false
http "$APP_URL/marketplace/status" > /dev/null
DEG=$(jget "degraded")
assert_eq "$DEG" "False" "/marketplace/status .degraded == false"

PIDX=$(jget "patent_index.load_error")
BIDX=$(jget "buyer_index.load_error")
[ "$PIDX" = "" ] && green "patent_index.load_error is null" \
  || red "patent_index.load_error = $PIDX"
[ "$BIDX" = "" ] && green "buyer_index.load_error is null" \
  || red "buyer_index.load_error = $BIDX"

# 2b. /health reports marketplace_embeddings_degraded=false
http "$APP_URL/health" > /dev/null
HDEG=$(jget "marketplace_embeddings_degraded")
assert_eq "$HDEG" "False" "/health .marketplace_embeddings_degraded == false"

# 2c. Mode B with a seeded buyer profile returns status != "engine_unavailable".
# Create a temp buyer profile attached to admin so we can call Mode B.
# Body MUST be single-line ASCII: curl -d strips CR/LF, and multi-line + UTF-8
# em-dash combos have caused "error parsing the body" 400s in git-bash on
# Windows. Keep payloads boring.
http -X POST "$APP_URL/marketplace/buyers" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"org_name":"Smoke Buyer","industry":"Water Treatment","industries_of_interest":["chemicals"],"technical_areas":["membrane"],"use_cases":"Smoke test of recommendation engine readiness - this profile is created and deleted by smoke-test-production.sh on every CI run.","tech_maturity_preference":"proven","budget_band":"medium","geographic_scope":["India"]}' > /dev/null
SMOKE_BUYER_ID=$(jget "buyer_id")
[ -n "$SMOKE_BUYER_ID" ] && green "Created smoke buyer profile $SMOKE_BUYER_ID" \
  || red "Failed to create smoke buyer profile: $(head -c 200 ${RESP_FILE})"

http -X POST "$APP_URL/marketplace/buyer/recommendations" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"top_k":3}' > /dev/null
MB_STATUS=$(jget "status")
assert_ne "$MB_STATUS" "engine_unavailable" "Mode B .status != engine_unavailable"

# ─── Section M3: Lifecycle security assertions (the four guards) ─────────
header "Marketplace — lifecycle security"

# Pick a draft listing owned by the inventor's professor to exercise transitions on.
http -X GET "$APP_URL/marketplace/inventor/listings" \
  -H "Authorization: Bearer $INVENTOR_TOKEN" > /dev/null
DRAFT_LISTING=$(python -c "
import json
d = json.load(open('${RESP_FILE}'))
for l in d.get('listings', []):
    if l.get('status') == 'draft':
        print(l['listing_id']); break
")
[ -n "$DRAFT_LISTING" ] && green "Located test draft: $DRAFT_LISTING" \
  || red "No draft listings found for inventor — seed data missing?"

# 3a. inventor cannot skip pending_approval: draft -> active is not in the state machine
HTTP_CODE=$(http -X POST "$APP_URL/marketplace/listings/$DRAFT_LISTING/transition" \
  -H "Authorization: Bearer $INVENTOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"active"}')
assert_status "$HTTP_CODE" "400" "(a) inventor draft->active rejected"
ERR=$(jget "error")
assert_eq "$ERR" "LISTING_NOT_ACTIVATABLE" "    error code is LISTING_NOT_ACTIVATABLE"

# 3c. inquiry on a non-active listing rejected (current $DRAFT_LISTING is draft)
HTTP_CODE=$(http -X POST "$APP_URL/marketplace/listings/$DRAFT_LISTING/inquiry" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"smoke test inquiry on a draft listing - should be rejected"}')
assert_status "$HTTP_CODE" "409" "(c) inquiry on non-active listing rejected"
ERR=$(jget "error")
assert_eq "$ERR" "LISTING_INACTIVE" "    error code is LISTING_INACTIVE"

# 3d. non-owner cannot read a draft (guest path returns LISTING_NOT_FOUND to avoid leaking existence)
HTTP_CODE=$(http "$APP_URL/marketplace/listings/$DRAFT_LISTING")
assert_status "$HTTP_CODE" "404" "(d) guest reads draft -> 404 (no existence leak)"
ERR=$(jget "error")
assert_eq "$ERR" "LISTING_NOT_FOUND" "    error code is LISTING_NOT_FOUND"

# 3b. stub-owned listings require admin to activate.
# Bootstrap: register an ephemeral professor_user, claim the stub profile,
# admin approves, then try to transition a stub listing -> expect 403.
http -X POST "$APP_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$STUB_CLAIMANT_EMAIL\",\"password\":\"$EPHEMERAL_PW\",\"name\":\"Stub Claimant\",\"role\":\"professor_user\"}" > /dev/null
STUB_TOKEN=$(mp_login "$STUB_CLAIMANT_EMAIL" "$EPHEMERAL_PW")
http -X POST "$APP_URL/marketplace/inventor/claim" \
  -H "Authorization: Bearer $STUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"professor_id\":\"$STUB_PROF_ID\"}" > /dev/null
STUB_CLAIM_ID=$(jget "claim_id")
http -X POST "$APP_URL/marketplace/admin/claim-requests/$STUB_CLAIM_ID/review" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"approve":true}' > /dev/null
[ "$?" = "0" ] && green "Stub claim bootstrapped (approved by admin)" || red "Stub claim bootstrap failed"

# Find a draft owned by the stub
http -X GET "$APP_URL/marketplace/inventor/listings" \
  -H "Authorization: Bearer $STUB_TOKEN" > /dev/null
STUB_DRAFT=$(python -c "
import json
d = json.load(open('${RESP_FILE}'))
for l in d.get('listings', []):
    if l.get('status') == 'draft':
        print(l['listing_id']); break
")
HTTP_CODE=$(http -X POST "$APP_URL/marketplace/listings/$STUB_DRAFT/transition" \
  -H "Authorization: Bearer $STUB_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"pending_approval"}')
assert_status "$HTTP_CODE" "403" "(b) stub-linked non-admin transition rejected"
ERR=$(jget "error")
assert_eq "$ERR" "STUB_REQUIRES_ADMIN_ACTIVATION" "    error code is STUB_REQUIRES_ADMIN_ACTIVATION"

# ─── Section M4: Claim-approval guard ────────────────────────────────────
header "Marketplace — claim-approval guard"
# Register a totally fresh professor_user. They should NOT see any listings until admin approval.
http -X POST "$APP_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$FRESH_PROF_EMAIL\",\"password\":\"$EPHEMERAL_PW\",\"name\":\"Fresh Professor\",\"role\":\"professor_user\"}" > /dev/null
FRESH_TOKEN=$(mp_login "$FRESH_PROF_EMAIL" "$EPHEMERAL_PW")
[ -n "$FRESH_TOKEN" ] && green "Fresh professor registered" || red "Register fresh professor failed"

# Submit claim
http -X POST "$APP_URL/marketplace/inventor/claim" \
  -H "Authorization: Bearer $FRESH_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"professor_id\":\"$INVENTOR_PROF_ID\"}" > /dev/null
CL_STATUS=$(jget "status")
assert_eq "$CL_STATUS" "pending" "Claim creates a pending request (not auto-linked)"

# Verify fresh user sees zero listings + claim_state=pending
http -X GET "$APP_URL/marketplace/inventor/listings" \
  -H "Authorization: Bearer $FRESH_TOKEN" > /dev/null
LINKED=$(jget "linked_professor_id")
CLAIM_STATE=$(jget "claim_state")
N_LIST=$(python -c "import json; print(len(json.load(open('${RESP_FILE}')).get('listings',[])))")
assert_eq "$LINKED" "" "Fresh user has no linked_professor_id"
assert_eq "$CLAIM_STATE" "pending" "claim_state = pending"
assert_eq "$N_LIST" "0" "Fresh user sees 0 listings (gate enforced)"

# ─── Section M5: Buyer-facing endpoints ─────────────────────────────────
header "Marketplace — buyer-facing endpoints"

# Activate a clean test listing through the real flow so browse + Mode B + inquiry have material.
# Pick a different draft than the one we used above so the assertions stay isolated.
TEST_LISTING=$(python <<PYEOF
import json, urllib.request
req = urllib.request.Request("$APP_URL/marketplace/inventor/listings",
    headers={"Authorization": "Bearer $INVENTOR_TOKEN"})
d = json.loads(urllib.request.urlopen(req).read())
drafts = [l['listing_id'] for l in d.get('listings', []) if l.get('status') == 'draft']
# pick the second one so it isn't $DRAFT_LISTING
print(drafts[1] if len(drafts) > 1 else (drafts[0] if drafts else ""))
PYEOF
)
[ -n "$TEST_LISTING" ] && green "Picked test listing: $TEST_LISTING" || red "No usable draft listing"

http -X POST "$APP_URL/marketplace/listings/$TEST_LISTING/transition" \
  -H "Authorization: Bearer $INVENTOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"pending_approval"}' > /dev/null
http -X POST "$APP_URL/marketplace/listings/$TEST_LISTING/transition" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target_status":"active"}' > /dev/null
NEWS=$(jget "new_status")
assert_eq "$NEWS" "active" "Listing transitioned to active"
TOUCHED_LISTINGS+=("$TEST_LISTING")
ACTIVE_LISTING_ID="$TEST_LISTING"

# Browse — public, only active
http "$APP_URL/marketplace/listings" > /dev/null
ONLY_ACTIVE=$(python -c "
import json
d = json.load(open('${RESP_FILE}'))
ls = d.get('listings', [])
print('yes' if ls and all(l.get('status')=='active' for l in ls) else 'no')
")
assert_eq "$ONLY_ACTIVE" "yes" "Public browse returns only active listings"

# Buyer profile CRUD (already created in M2; verify GET reads it back)
http "$APP_URL/marketplace/buyers/me" \
  -H "Authorization: Bearer $ADMIN_TOKEN" > /dev/null
GOT_BID=$(jget "buyer_id")
assert_eq "$GOT_BID" "$SMOKE_BUYER_ID" "GET /marketplace/buyers/me returns the just-created profile"

# Mode B returns ok with actual candidates (we just activated one)
http -X POST "$APP_URL/marketplace/buyer/recommendations" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"top_k":5}' > /dev/null
MB_S=$(jget "status")
MB_N=$(python -c "import json; print(len(json.load(open('${RESP_FILE}')).get('candidates',[])))")
assert_eq "$MB_S" "ok" "Mode B status = ok"
[ "$MB_N" -ge 1 ] && green "Mode B returned $MB_N candidate(s)" \
  || red "Mode B returned 0 candidates despite an active listing"

# Inquiry round-trip
http -X POST "$APP_URL/marketplace/listings/$ACTIVE_LISTING_ID/inquiry" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message":"Smoke-test inquiry - created and resolved by smoke-test-production.sh."}' > /dev/null
INQUIRY_ID=$(jget "inquiry_id")
[ -n "$INQUIRY_ID" ] && green "Inquiry created: $INQUIRY_ID" || red "Inquiry create failed"

http "$APP_URL/marketplace/inbox" \
  -H "Authorization: Bearer $ADMIN_TOKEN" > /dev/null
SENT_COUNT=$(jget "counts.sent")
[ "$SENT_COUNT" -ge 1 ] && green "Buyer inbox shows sent inquiry" \
  || red "Buyer inbox missing the sent inquiry (counts.sent=$SENT_COUNT)"

http -X POST "$APP_URL/marketplace/inquiries/$INQUIRY_ID/respond" \
  -H "Authorization: Bearer $INVENTOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"accepted"}' > /dev/null
NEW_INQ=$(jget "status")
assert_eq "$NEW_INQ" "accepted" "Inventor responds to inquiry -> accepted"

# ─── Cleanup ─────────────────────────────────────────────────────────────
header "Marketplace — cleanup"
# Reset everything via direct DB. We *could* drive the state machine (active ->
# paused -> withdrawn) but withdrawn is terminal — that depletes the pool of
# draft listings across runs. SQL reset keeps the pool fresh. This block only
# runs when COLLABV_DB_PATH (or default ./collabv_data.db) is reachable from
# the smoke host — fine for local CI; a remote prod smoke would skip this.
DB_PATH="${COLLABV_DB_PATH:-./collabv_data.db}"
if [ -f "$DB_PATH" ]; then
  TOUCHED_CSV=$(IFS=,; echo "${TOUCHED_LISTINGS[*]}")
  python <<PYEOF
import sqlite3
conn = sqlite3.connect("$DB_PATH")
# Reset any listings the run flipped to active, back to draft + clear lifecycle stamps
ids = [x for x in "$TOUCHED_CSV".split(",") if x]
for lid in ids:
    conn.execute("UPDATE patent_listings SET status='draft', activated_at=NULL, approved_at=NULL, approved_by_user_id=NULL WHERE listing_id=?", (lid,))
# Drop the run's smoke buyer profile (attached to admin)
admin_row = conn.execute("SELECT id FROM users WHERE email=?", ("$ADMIN_EMAIL",)).fetchone()
if admin_row:
    conn.execute("DELETE FROM buyer_profiles WHERE user_id=?", (admin_row[0],))
# Drop the run's inquiry
if "$INQUIRY_ID":
    conn.execute("DELETE FROM marketplace_inquiries WHERE inquiry_id=?", ("$INQUIRY_ID",))
# Drop ephemeral users + their claims + their inquiries
for em in ("$FRESH_PROF_EMAIL", "$STUB_CLAIMANT_EMAIL"):
    row = conn.execute("SELECT id FROM users WHERE email=?", (em,)).fetchone()
    if row:
        uid = row[0]
        conn.execute("DELETE FROM marketplace_inquiries WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM professor_claims WHERE user_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
conn.commit()
n_active = conn.execute("SELECT COUNT(*) FROM patent_listings WHERE status='active'").fetchone()[0]
print(f"  cleanup: reset {len(ids)} listing(s) to draft; ephemeral fixtures removed; active listings now {n_active}")
PYEOF
else
  echo "  cleanup: \$COLLABV_DB_PATH ($DB_PATH) not reachable; ephemeral fixtures remain in remote DB"
fi

header "Summary"
printf "  \033[32m%d passed\033[0m, \033[31m%d failed\033[0m\n" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
