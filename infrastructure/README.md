# CollabV AI - AWS Production Deployment

Terraform-defined infrastructure for production. Brings up VPC, ECS Fargate,
RDS (Postgres + pgvector), ElastiCache Redis, ALB, ACM SSL, ECR, S3, IAM,
and CloudWatch.

## Estimated monthly cost (ap-south-1)

| Component              | Spec               | ~Cost (USD) |
|------------------------|--------------------|-------------|
| RDS db.t3.medium MultiAZ | 50 GB encrypted  | $80         |
| ElastiCache cache.t3.micro | 1 node         | $12         |
| ECS Fargate 2 services | 0.5 vCPU / 1 GB ea | $40         |
| ALB                    | always-on          | $20         |
| NAT gateway            | single             | $32         |
| Data transfer + misc   | ~                  | $20         |
| **Total**              |                    | **~$200**   |

Lower it ~$120 by switching RDS to single-AZ and removing the NAT (use public
subnets for the tasks while still keeping a SG-locked-down ALB).

## Deploy

```bash
cd infrastructure
terraform init
terraform apply \
  -var="domain=app.yourcompany.com" \
  -var="hosted_zone_id=Z123456789ABC" \
  -var="db_password=<choose-strong-password>"
```

Terraform prints `ecr_backend`, `ecr_frontend`, `rds_endpoint`, etc. Use those
to push your Docker images and seed task definitions.

Push images:
```bash
aws ecr get-login-password | docker login --username AWS --password-stdin <ECR>
docker build -f Dockerfile.backend -t <ECR>/collabv-backend:latest .
docker push <ECR>/collabv-backend:latest
```

Then create ECS task definitions referencing these images plus the env vars:
```
DATABASE_URL=postgresql://collabv:PASSWORD@<rds-endpoint>:5432/collabv
REDIS_URL=redis://<redis-endpoint>:6379/0
ANTHROPIC_API_KEY=<your key>
ENABLE_EMBEDDINGS=true
ENABLE_LLM_EXPLAIN=true
```

## Scaling

- The default ECS service has 1 desired task. Increase `desired_count` and add
  CloudWatch alarms to autoscale on CPU > 70% or request count.
- RDS storage autoscaling is off by default; enable it in `aws_db_instance` if
  you expect significant growth.
- For multi-region disaster recovery, replicate the RDS via read replicas and
  the S3 artifacts bucket via cross-region replication.

## Disaster recovery

- RDS daily snapshots are kept for 7 days; restore via the AWS console or
  `terraform import` of a snapshot identifier.
- Embeddings index is rebuildable from the professor JSON via
  `POST /embeddings/rebuild` - keep that JSON safe in S3.
