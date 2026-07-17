# Secrets stored in AWS Secrets Manager and referenced by ECS task definitions.

resource "aws_secretsmanager_secret" "db_password" {
  name        = "${var.name}/db-password"
  description = "RDS master password"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = var.db_password
}

resource "aws_secretsmanager_secret" "anthropic_api_key" {
  name        = "${var.name}/anthropic-api-key"
  description = "Anthropic Claude API key"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret" "jwt_secret" {
  name        = "${var.name}/jwt-secret"
  description = "JWT signing secret"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "jwt_secret" {
  secret_id     = aws_secretsmanager_secret.jwt_secret.id
  secret_string = var.jwt_secret
}

variable "jwt_secret" {
  description = "JWT signing secret (>= 32 random bytes)"
  sensitive   = true
}

# Give the ECS task execution role permission to read these secrets.
resource "aws_iam_role_policy" "ecs_secrets" {
  name = "${var.name}-ecs-secrets"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.db_password.arn,
        aws_secretsmanager_secret.anthropic_api_key.arn,
        aws_secretsmanager_secret.jwt_secret.arn,
      ]
    }]
  })
}
