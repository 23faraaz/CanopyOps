locals {
  arn    = "arn:aws"
  outbox = "arn:aws:s3:::${var.name_prefix}-outbox-${var.account_id}"
}
resource "aws_iam_policy" "runtime_boundary" {
  name        = "${var.name_prefix}-runtime-boundary"
  description = "Maximum permissions for demo task/execution roles; does not grant permissions by itself"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["ecr:GetAuthorizationToken"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = [for service in ["api", "worker", "analytics"] : "${local.arn}:ecr:${var.aws_region}:${var.account_id}:repository/${var.name_prefix}/${service}"]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${local.arn}:logs:${var.aws_region}:${var.account_id}:log-group:/ecs/${var.name_prefix}-*:log-stream:*"
      },
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [for secret in ["api", "worker", "analytics", "redis"] : "${local.arn}:secretsmanager:${var.aws_region}:${var.account_id}:secret:${var.name_prefix}/${secret}-??????"]
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
        Resource = "${local.arn}:sqs:${var.aws_region}:${var.account_id}:${var.name_prefix}-readings"
      },
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = local.outbox
        Condition = { StringLike = { "s3:prefix" = "readings/v1/*" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${local.outbox}/readings/v1/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = "${local.arn}:kms:${var.aws_region}:${var.account_id}:key/*"
        Condition = {
          StringEquals = {
            "kms:ViaService"       = "s3.${var.aws_region}.amazonaws.com"
            "aws:ResourceTag/Name" = "${var.name_prefix}-outbox"
          }
          ArnLike = { "kms:EncryptionContext:aws:s3:arn" = [local.outbox, "${local.outbox}/*"] }
        }
      }
    ]
  })
  tags = merge(var.tags, { Name = "${var.name_prefix}-runtime-boundary", ManagedBy = "Terraform" })
}
