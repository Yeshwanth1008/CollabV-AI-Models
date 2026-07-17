#!/usr/bin/env bash
# CollabV AI - first-time production deployment.
#
# 1. Build + push Docker images to ECR
# 2. Run terraform init + plan + apply
# 3. Wait for RDS to become available
# 4. Run Alembic migrations against production DB
# 5. Migrate SQLite -> Postgres data
# 6. Seed initial admin user
# 7. Verify /health passes
# 8. Print app URL
#
# Re-runnable. Each step is idempotent.

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
PROJECT="${PROJECT:-collabv}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()  { printf "\033[31m%s\033[0m\n" "$*"; }
note() { printf "  • %s\n" "$*"; }

# Load .env.production (DOMAIN_NAME, HOSTED_ZONE_ID, JWT_SECRET, DB_PASSWORD, etc.)
if [ ! -f ".env.production" ]; then
  red ".env.production missing. Run scripts/setup-aws.sh first."
  exit 1
fi
# shellcheck disable=SC1091
set -a; source .env.production; set +a

: "${DOMAIN_NAME:?DOMAIN_NAME must be set in .env.production}"
: "${HOSTED_ZONE_ID:?HOSTED_ZONE_ID must be set in .env.production}"
: "${JWT_SECRET:?JWT_SECRET must be set}"
: "${DB_PASSWORD:?DB_PASSWORD must be set (use a strong random password)}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_HOST="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
IMG_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M%S)}"

# ─── Step 1: Build + push images ─────────────────────────────────────────

cyan "[1/8] Build + push Docker images (tag: $IMG_TAG)"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_HOST"

note "Building backend..."
docker build -f Dockerfile.backend -t "$ECR_HOST/${PROJECT}-backend:$IMG_TAG" -t "$ECR_HOST/${PROJECT}-backend:latest" .
docker push "$ECR_HOST/${PROJECT}-backend:$IMG_TAG"
docker push "$ECR_HOST/${PROJECT}-backend:latest"

note "Building frontend..."
docker build -f frontend/Dockerfile \
  --build-arg NEXT_PUBLIC_API_BASE="https://$DOMAIN_NAME" \
  -t "$ECR_HOST/${PROJECT}-frontend:$IMG_TAG" \
  -t "$ECR_HOST/${PROJECT}-frontend:latest" frontend
docker push "$ECR_HOST/${PROJECT}-frontend:$IMG_TAG"
docker push "$ECR_HOST/${PROJECT}-frontend:latest"

# ─── Step 2: Terraform apply ─────────────────────────────────────────────

cyan "[2/8] Terraform apply"

cd infrastructure
terraform init -upgrade
terraform plan \
  -var "domain=$DOMAIN_NAME" \
  -var "hosted_zone_id=$HOSTED_ZONE_ID" \
  -var "db_password=$DB_PASSWORD" \
  -var "jwt_secret=$JWT_SECRET" \
  -var "backend_image_tag=$IMG_TAG" \
  -var "frontend_image_tag=$IMG_TAG" \
  -out=tfplan
terraform apply -auto-approve tfplan
cd "$ROOT"

ALB_DNS=$(cd infrastructure && terraform output -raw alb_dns)
RDS_HOST=$(cd infrastructure && terraform output -raw rds_endpoint | cut -d: -f1)
note "ALB: $ALB_DNS"
note "RDS: $RDS_HOST"

# ─── Step 3: Wait for RDS ────────────────────────────────────────────────

cyan "[3/8] Wait for RDS to become available"
aws rds wait db-instance-available --db-instance-identifier "${PROJECT}-db" --region "$REGION"
green "  RDS ready"

# ─── Step 4: Alembic migrations ─────────────────────────────────────────

cyan "[4/8] Run Alembic migrations"
export DATABASE_URL="postgresql+asyncpg://collabv:$DB_PASSWORD@$RDS_HOST:5432/collabv"

# Run from local machine - either we have access via VPN/bastion, or we run a
# one-off ECS task. By default, try locally - if it fails, fall back to ECS
# run-task with the same image.
if command -v alembic >/dev/null 2>&1; then
  if alembic upgrade head 2>/dev/null; then
    green "  Migrations applied (local)"
  else
    note "Local alembic couldn't reach RDS. Running migration in ECS..."
    aws ecs run-task --cluster "${PROJECT}-prod" \
      --launch-type FARGATE \
      --network-configuration "awsvpcConfiguration={subnets=[$(cd infrastructure && terraform output -json | jq -r '.private_subnets.value | join(",")')]}" \
      --overrides '{"containerOverrides":[{"name":"backend","command":["alembic","upgrade","head"]}]}' \
      --task-definition "${PROJECT}-backend" \
      --region "$REGION" >/dev/null
    green "  Migration task submitted"
  fi
else
  note "alembic not installed locally - skip; the backend container will run migrations on boot"
fi

# ─── Step 5: Migrate SQLite -> Postgres ─────────────────────────────────

cyan "[5/8] Migrate existing SQLite data to PostgreSQL"
if [ -f "collabv_data.db" ]; then
  python scripts/migrate_sqlite_to_postgres.py \
    --sqlite collabv_data.db \
    --professors iitm_professors_with_patents.json \
    --faiss-index collabv_embeddings.index 2>&1 | tail -10 || note "Migration script failed (run manually if needed)"
else
  note "No collabv_data.db found - starting fresh"
fi

# ─── Step 6: Seed admin user ────────────────────────────────────────────

cyan "[6/8] Seed initial admin user"
APP_URL="https://$DOMAIN_NAME"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@$DOMAIN_NAME}"
ADMIN_PASSWORD=$(python -c "import secrets;print(secrets.token_urlsafe(16))")
note "Admin email: $ADMIN_EMAIL"
note "Admin password (save this): $ADMIN_PASSWORD"

# Wait for the service to come up
note "Waiting for $APP_URL/health..."
for i in {1..60}; do
  if curl -fsS "$APP_URL/health" >/dev/null 2>&1; then break; fi
  sleep 5
done

# Register the admin (idempotent if it already exists)
curl -fsS -X POST "$APP_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\",\"name\":\"Admin\",\"role\":\"admin\"}" \
  || note "Admin may already exist - continuing"

# ─── Step 7-8: Health + summary ─────────────────────────────────────────

cyan "[7/8] Health check"
HEALTH=$(curl -sS "$APP_URL/health" || true)
echo "$HEALTH"
if echo "$HEALTH" | grep -q '"status":"ok"'; then
  green "  Health check passed"
else
  red "  Health check FAILED - investigate ECS logs"
fi

cyan "[8/8] Done"
green "App URL : $APP_URL"
green "API docs: $APP_URL/docs"
echo
echo "Admin credentials:"
echo "  email   : $ADMIN_EMAIL"
echo "  password: $ADMIN_PASSWORD  (SAVE THIS - it is not stored anywhere)"
echo
echo "Next steps:"
echo "  - Run ./scripts/smoke-test-production.sh to verify everything"
echo "  - When you have the real IITM patent API URL, run patent_scraper with --api-url"
