# CollabV AI - production infrastructure (Terraform / AWS)
#
# Builds: VPC, ECS Fargate cluster, RDS Postgres (pgvector), ElastiCache Redis,
# S3 bucket, ALB + ACM SSL, Route53 record, ECR repos, IAM roles, CloudWatch.
#
# Usage:
#   terraform init
#   terraform apply -var="domain=app.yourdomain.com" -var="hosted_zone_id=Z..."

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.30" }
  }
}

provider "aws" {
  region = var.region
}

# ─── Variables ─────────────────────────────────────────────────────────────

variable "region"        { default = "ap-south-1" }
variable "name"          { default = "collabv" }
variable "domain"        { description = "Public domain for the app" }
variable "hosted_zone_id" { description = "Route53 hosted zone for the domain" }
variable "db_password"   { sensitive = true }

# ─── VPC ───────────────────────────────────────────────────────────────────

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.5"

  name = "${var.name}-vpc"
  cidr = "10.0.0.0/16"

  azs             = ["${var.region}a", "${var.region}b"]
  private_subnets = ["10.0.1.0/24", "10.0.2.0/24"]
  public_subnets  = ["10.0.101.0/24", "10.0.102.0/24"]

  enable_nat_gateway   = true
  single_nat_gateway   = true
  enable_dns_hostnames = true
}

# ─── ECR ───────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "backend" {
  name                 = "${var.name}-backend"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_repository" "frontend" {
  name                 = "${var.name}-frontend"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration { scan_on_push = true }
}

# ─── RDS Postgres with pgvector ───────────────────────────────────────────

resource "aws_security_group" "rds" {
  name   = "${var.name}-rds"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.name}-db-subnets"
  subnet_ids = module.vpc.private_subnets
}

variable "db_instance_class" {
  default = "db.t3.medium"
}

# Parameter group that exposes the pgvector extension to clients.
resource "aws_db_parameter_group" "main" {
  name        = "${var.name}-pg16"
  family      = "postgres16"
  description = "CollabV AI - allow pgvector + slow-query logging"

  parameter {
    name  = "shared_preload_libraries"
    value = "pg_stat_statements"
    apply_method = "pending-reboot"
  }
  parameter {
    name  = "log_min_duration_statement"
    value = "500"
  }
}

resource "aws_db_instance" "main" {
  identifier              = "${var.name}-db"
  engine                  = "postgres"
  engine_version          = "16.4"
  instance_class          = var.db_instance_class
  allocated_storage       = 50
  max_allocated_storage   = 200          # autoscale up to 200 GB
  storage_encrypted       = true
  storage_type            = "gp3"
  multi_az                = true
  db_name                 = "collabv"
  username                = "collabv"
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  parameter_group_name    = aws_db_parameter_group.main.name
  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"
  performance_insights_enabled = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  skip_final_snapshot     = false
  final_snapshot_identifier = "${var.name}-final-${formatdate("YYYYMMDDhhmm", timestamp())}"
  deletion_protection     = true
  apply_immediately       = false
  lifecycle {
    ignore_changes = [final_snapshot_identifier]
  }
}

# ─── ElastiCache Redis ────────────────────────────────────────────────────

resource "aws_security_group" "redis" {
  name   = "${var.name}-redis"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.name}-redis-subnets"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_cluster" "main" {
  cluster_id           = "${var.name}-redis"
  engine               = "redis"
  node_type            = "cache.t3.micro"
  num_cache_nodes      = 1
  parameter_group_name = "default.redis7"
  port                 = 6379
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.redis.id]
}

# ─── S3 (artifacts) ───────────────────────────────────────────────────────

resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.name}-artifacts-${random_id.suffix.hex}"
}

resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

# ─── ECS cluster + IAM ────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${var.name}-prod"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_security_group" "ecs" {
  name   = "${var.name}-ecs"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port       = 0
    to_port         = 65535
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.name}-ecs-task-execution"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# ─── ALB ───────────────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name   = "${var.name}-alb"
  vpc_id = module.vpc.vpc_id

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb" "main" {
  name               = "${var.name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = module.vpc.public_subnets
}

# ─── ACM cert ──────────────────────────────────────────────────────────────

resource "aws_acm_certificate" "main" {
  domain_name       = var.domain
  validation_method = "DNS"
  lifecycle { create_before_destroy = true }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for dvo in aws_acm_certificate.main.domain_validation_options :
    dvo.domain_name => dvo
  }
  zone_id = var.hosted_zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 60
}

resource "aws_acm_certificate_validation" "main" {
  certificate_arn         = aws_acm_certificate.main.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

resource "aws_route53_record" "alb" {
  zone_id = var.hosted_zone_id
  name    = var.domain
  type    = "A"
  alias {
    name                   = aws_lb.main.dns_name
    zone_id                = aws_lb.main.zone_id
    evaluate_target_health = true
  }
}

# ─── CloudWatch log groups ────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${var.name}-backend"
  retention_in_days = 30
}

resource "aws_cloudwatch_log_group" "frontend" {
  name              = "/ecs/${var.name}-frontend"
  retention_in_days = 30
}

# ─── Outputs ──────────────────────────────────────────────────────────────

output "alb_dns"        { value = aws_lb.main.dns_name }
output "ecr_backend"    { value = aws_ecr_repository.backend.repository_url }
output "ecr_frontend"   { value = aws_ecr_repository.frontend.repository_url }
output "rds_endpoint"   { value = aws_db_instance.main.endpoint }
output "redis_endpoint" { value = aws_elasticache_cluster.main.cache_nodes[0].address }
output "s3_bucket"      { value = aws_s3_bucket.artifacts.bucket }
output "cluster_name"   { value = aws_ecs_cluster.main.name }
output "backend_service"  { value = aws_ecs_service.backend.name }
output "frontend_service" { value = aws_ecs_service.frontend.name }
output "app_url"        { value = "https://${var.domain}" }

# Nameservers to set at your domain registrar. Only relevant if you created the
# hosted zone in this account; if you provided an existing hosted_zone_id, you
# probably already pointed your registrar at AWS.
data "aws_route53_zone" "main" {
  zone_id = var.hosted_zone_id
}
output "nameservers" {
  value       = data.aws_route53_zone.main.name_servers
  description = "Point your domain registrar at these nameservers"
}
