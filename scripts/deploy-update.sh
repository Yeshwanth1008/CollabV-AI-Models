#!/usr/bin/env bash
# CollabV AI - rolling production update (subsequent deploys).
#
# 1. Build images and push to ECR with new tag
# 2. Update ECS task definitions to point at new tag
# 3. Force a new deployment (blue/green via ECS circuit breaker)
# 4. Wait for both services to stabilize
# 5. Run pending alembic migrations
# 6. Sanity-check /health

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
PROJECT="${PROJECT:-collabv}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
note() { printf "  • %s\n" "$*"; }

[ -f .env.production ] || { echo ".env.production missing"; exit 1; }
# shellcheck disable=SC1091
set -a; source .env.production; set +a

: "${DOMAIN_NAME:?DOMAIN_NAME must be set}"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
ECR_HOST="$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com"
IMG_TAG="${IMAGE_TAG:-$(git rev-parse --short HEAD 2>/dev/null || date +%Y%m%d-%H%M%S)}"

cyan "[1/4] Build + push images (tag: $IMG_TAG)"

aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ECR_HOST"

docker build -f Dockerfile.backend -t "$ECR_HOST/${PROJECT}-backend:$IMG_TAG" .
docker push "$ECR_HOST/${PROJECT}-backend:$IMG_TAG"

docker build -f frontend/Dockerfile \
  --build-arg NEXT_PUBLIC_API_BASE="https://$DOMAIN_NAME" \
  -t "$ECR_HOST/${PROJECT}-frontend:$IMG_TAG" frontend
docker push "$ECR_HOST/${PROJECT}-frontend:$IMG_TAG"

cyan "[2/4] Terraform apply (new task definitions)"
cd infrastructure
terraform apply -auto-approve \
  -var "domain=$DOMAIN_NAME" \
  -var "hosted_zone_id=$HOSTED_ZONE_ID" \
  -var "db_password=$DB_PASSWORD" \
  -var "jwt_secret=$JWT_SECRET" \
  -var "backend_image_tag=$IMG_TAG" \
  -var "frontend_image_tag=$IMG_TAG"
cd "$ROOT"

cyan "[3/4] Force ECS to pull new images"
aws ecs update-service --cluster "${PROJECT}-prod" --service "${PROJECT}-backend" --force-new-deployment --region "$REGION" >/dev/null
aws ecs update-service --cluster "${PROJECT}-prod" --service "${PROJECT}-frontend" --force-new-deployment --region "$REGION" >/dev/null

note "Waiting for services to stabilize..."
aws ecs wait services-stable --cluster "${PROJECT}-prod" \
  --services "${PROJECT}-backend" "${PROJECT}-frontend" --region "$REGION"
green "  Both services stable"

cyan "[4/4] Health check"
APP_URL="https://$DOMAIN_NAME"
if curl -fsS "$APP_URL/health" | grep -q '"status":"ok"'; then
  green "  $APP_URL/health -> ok"
else
  echo "WARN: health check non-200, inspect ECS logs"
fi

green "Deployed tag: $IMG_TAG"
