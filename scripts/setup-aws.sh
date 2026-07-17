#!/usr/bin/env bash
# CollabV AI - one-time AWS account setup.
#
# Run this BEFORE deploy.sh. It:
#   1. Verifies AWS CLI + credentials
#   2. Creates Terraform state bucket (versioned + encrypted)
#   3. Creates ECR repositories
#   4. Prompts for production secrets and stores them in Secrets Manager
#   5. Writes .env.production from .env.production.example
#
# Idempotent - safe to re-run.

set -euo pipefail

REGION="${AWS_REGION:-ap-south-1}"
PROJECT="${PROJECT:-collabv}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:-${PROJECT}-tf-state-$(aws sts get-caller-identity --query Account --output text 2>/dev/null || echo MISSING)}"

cyan() { printf "\033[36m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
red()   { printf "\033[31m%s\033[0m\n" "$*"; }
note()  { printf "  • %s\n" "$*"; }

# ─── Pre-flight ───────────────────────────────────────────────────────────

cyan "[1/5] Pre-flight checks"

command -v aws >/dev/null    || { red "aws CLI not installed";    exit 1; }
command -v terraform >/dev/null || { red "terraform not installed"; exit 1; }
command -v docker >/dev/null || { red "docker not installed";     exit 1; }

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  red "AWS credentials not configured. Run: aws configure"
  exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
note "AWS account: $ACCOUNT_ID"
note "Region: $REGION"

# ─── Terraform state bucket ──────────────────────────────────────────────

cyan "[2/5] Terraform state bucket"

TF_STATE_BUCKET="${PROJECT}-tf-state-${ACCOUNT_ID}"
if aws s3api head-bucket --bucket "$TF_STATE_BUCKET" --region "$REGION" 2>/dev/null; then
  note "Bucket already exists: $TF_STATE_BUCKET"
else
  note "Creating bucket: $TF_STATE_BUCKET"
  if [ "$REGION" = "us-east-1" ]; then
    aws s3api create-bucket --bucket "$TF_STATE_BUCKET" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$TF_STATE_BUCKET" --region "$REGION" \
        --create-bucket-configuration LocationConstraint="$REGION"
  fi
  aws s3api put-bucket-versioning --bucket "$TF_STATE_BUCKET" \
      --versioning-configuration Status=Enabled
  aws s3api put-bucket-encryption --bucket "$TF_STATE_BUCKET" \
      --server-side-encryption-configuration \
      '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  aws s3api put-public-access-block --bucket "$TF_STATE_BUCKET" \
      --public-access-block-configuration \
      "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
fi

cat > infrastructure/backend.tf <<EOF
terraform {
  backend "s3" {
    bucket = "$TF_STATE_BUCKET"
    key    = "$PROJECT/terraform.tfstate"
    region = "$REGION"
    encrypt = true
  }
}
EOF
note "Wrote infrastructure/backend.tf"

# ─── ECR repositories ────────────────────────────────────────────────────

cyan "[3/5] ECR repositories"

for repo in "${PROJECT}-backend" "${PROJECT}-frontend"; do
  if aws ecr describe-repositories --repository-names "$repo" --region "$REGION" >/dev/null 2>&1; then
    note "Already exists: $repo"
  else
    aws ecr create-repository \
        --repository-name "$repo" \
        --region "$REGION" \
        --image-scanning-configuration scanOnPush=true \
        --image-tag-mutability MUTABLE >/dev/null
    note "Created: $repo"
  fi
done

# ─── Prompt for secrets ──────────────────────────────────────────────────

cyan "[4/5] Production secrets"

env_file=".env.production"
if [ -f "$env_file" ]; then
  note ".env.production already exists - skipping prompt"
else
  cp .env.production.example "$env_file"
  note "Created $env_file from template"
  echo
  echo "  Open $env_file and fill in:"
  echo "    ANTHROPIC_API_KEY"
  echo "    JWT_SECRET (generate: python -c \"import secrets;print(secrets.token_urlsafe(64))\")"
  echo "    DATABASE_URL (will be filled in after deploy)"
  echo "    DOMAIN_NAME"
fi

# Put secrets into AWS Secrets Manager (created idempotently)
put_secret() {
  local name="$1"
  local value="$2"
  if aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" >/dev/null 2>&1; then
    aws secretsmanager put-secret-value --secret-id "$name" --secret-string "$value" --region "$REGION" >/dev/null
    note "Updated: $name"
  else
    aws secretsmanager create-secret --name "$name" --secret-string "$value" --region "$REGION" >/dev/null
    note "Created: $name"
  fi
}

if [ -f "$env_file" ]; then
  # shellcheck disable=SC1090
  set -a; source "$env_file"; set +a

  [ -n "${ANTHROPIC_API_KEY:-}" ] && put_secret "${PROJECT}/anthropic-api-key" "$ANTHROPIC_API_KEY"
  [ -n "${JWT_SECRET:-}"        ] && put_secret "${PROJECT}/jwt-secret" "$JWT_SECRET"
  [ -n "${DB_PASSWORD:-}"       ] && put_secret "${PROJECT}/db-password" "$DB_PASSWORD"
fi

# ─── Summary ─────────────────────────────────────────────────────────────

cyan "[5/5] Setup complete"
green "  Terraform state: s3://$TF_STATE_BUCKET"
green "  ECR backend   : $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/${PROJECT}-backend"
green "  ECR frontend  : $ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com/${PROJECT}-frontend"
echo
echo "Next: edit .env.production (especially DOMAIN_NAME and HOSTED_ZONE_ID), then run:"
echo "  ./scripts/deploy.sh"
