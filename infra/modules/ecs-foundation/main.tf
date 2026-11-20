data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
locals {
  components  = toset(["api", "worker", "analytics", "generator"])
  credentials = toset(["api", "worker", "analytics", "redis"])
  prefix      = "arn:${data.aws_partition.current.partition}"
  account     = data.aws_caller_identity.current.account_id
  log_arns = {
    for component in local.components : component => "${local.prefix}:logs:${var.aws_region}:${local.account}:log-group:/ecs/${var.name_prefix}-${component}"
  }
  task_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account }
        ArnLike      = { "aws:SourceArn" = "${local.prefix}:ecs:${var.aws_region}:${local.account}:*" }
      }
    }]
  })
}
resource "aws_ecs_cluster" "this" {
  name = var.name_prefix
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
  tags = merge(var.tags, { Name = var.name_prefix, ManagedBy = "Terraform" })
}
resource "aws_kms_key" "logs" {
  description             = "CanopyOps bounded CloudWatch log encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AccountIamDelegation"
        Effect    = "Allow"
        Principal = { AWS = "${local.prefix}:iam::${local.account}:root" }
        Action    = "kms:*"
        Resource  = "*"
      },
      {
        Sid       = "RegionalApplicationLogs"
        Effect    = "Allow"
        Principal = { Service = "logs.${var.aws_region}.amazonaws.com" }
        Action    = ["kms:Encrypt", "kms:Decrypt", "kms:ReEncrypt*", "kms:GenerateDataKey*", "kms:DescribeKey"]
        Resource  = "*"
        Condition = {
          ArnLike = {
            "kms:EncryptionContext:aws:logs:arn" = concat(values(local.log_arns), [
              "${local.prefix}:logs:${var.aws_region}:${local.account}:log-group:/aws/rds/instance/${var.name_prefix}-postgres/*",
              "${local.prefix}:logs:${var.aws_region}:${local.account}:log-group:aws-waf-logs-${var.name_prefix}"
            ])
          }
        }
      }
    ]
  })
  tags = merge(var.tags, { Name = "${var.name_prefix}-logs", ManagedBy = "Terraform" })
}
resource "aws_kms_alias" "logs" {
  name          = "alias/${var.name_prefix}-logs"
  target_key_id = aws_kms_key.logs.key_id
}
resource "aws_s3_bucket" "alb_logs" {
  bucket        = "${var.name_prefix}-alb-logs-${local.account}"
  force_destroy = false
  tags          = merge(var.tags, { Name = "${var.name_prefix}-alb-logs", ManagedBy = "Terraform" })
}
resource "aws_s3_bucket_public_access_block" "alb_logs" {
  bucket                  = aws_s3_bucket.alb_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  versioning_configuration { status = "Enabled" }
}
# Approved ALB-only applicability exception: AWS log delivery supports SSE-S3 only.
# https://docs.aws.amazon.com/elasticloadbalancing/latest/application/enable-access-logging.html
# Recheck vendor support before expiry; no other bucket is covered.
#trivy:ignore:AVD-AWS-0132:exp:2026-12-19
resource "aws_s3_bucket_server_side_encryption_configuration" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  # ALB log delivery supports SSE-S3, not SSE-KMS.
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "alb_logs" {
  bucket     = aws_s3_bucket.alb_logs.id
  depends_on = [aws_s3_bucket_versioning.alb_logs]
  rule {
    id     = "bounded-rehearsal-logs"
    status = "Enabled"
    filter { prefix = "" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 7 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_s3_bucket_policy" "alb_logs" {
  bucket = aws_s3_bucket.alb_logs.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.alb_logs.arn, "${aws_s3_bucket.alb_logs.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "AccountAlbDelivery"
        Effect    = "Allow"
        Principal = { Service = "logdelivery.elasticloadbalancing.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.alb_logs.arn}/${var.name_prefix}/AWSLogs/${local.account}/*"
        Condition = {
          ArnLike = { "aws:SourceArn" = "${local.prefix}:elasticloadbalancing:${var.aws_region}:${local.account}:loadbalancer/app/${var.name_prefix}-alb/*" }
        }
      }
    ]
  })
}
resource "aws_secretsmanager_secret" "runtime" {
  for_each                = local.credentials
  name                    = "${var.name_prefix}/${each.key}"
  description             = "Runtime ${each.key} credential; provision versions outside Terraform"
  recovery_window_in_days = 7
  tags                    = merge(var.tags, { Name = "${var.name_prefix}-${each.key}", ManagedBy = "Terraform" })
}
resource "aws_iam_role" "execution" {
  for_each             = local.components
  name                 = "${var.name_prefix}-${each.key}-execution"
  assume_role_policy   = local.task_trust
  permissions_boundary = var.permissions_boundary_arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-${each.key}-execution", ManagedBy = "Terraform" })
}
resource "aws_iam_role" "task" {
  for_each             = local.components
  name                 = "${var.name_prefix}-${each.key}-task"
  assume_role_policy   = local.task_trust
  permissions_boundary = var.permissions_boundary_arn
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-${each.key}-task", ManagedBy = "Terraform" })
}
resource "aws_iam_role_policy" "execution" {
  for_each = local.components
  name     = "runtime-startup"
  role     = aws_iam_role.execution[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
        Resource = var.repository_arns[each.key == "generator" ? "worker" : each.key]
      },
      {
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "${local.log_arns[each.key]}:log-stream:*"
      }
      ], each.key == "generator" ? [] : [{
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = concat([aws_secretsmanager_secret.runtime[each.key].arn], each.key == "analytics" ? [aws_secretsmanager_secret.runtime["redis"].arn] : [])
    }])
  })
}
resource "aws_iam_role_policy" "queue" {
  for_each = toset(["worker", "generator"])
  name     = "ingestion-queue"
  role     = aws_iam_role.task[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = each.key == "generator" ? ["sqs:SendMessage", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"] : ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes", "sqs:GetQueueUrl"]
      Resource = var.queue_arn
    }]
  })
}
resource "aws_elasticache_user_group" "analytics" {
  count         = length(var.redis_user_ids) == 0 ? 0 : 1
  engine        = "redis"
  user_group_id = "${var.name_prefix}-analytics"
  user_ids      = var.redis_user_ids
  tags          = merge(var.tags, { Name = "${var.name_prefix}-analytics", ManagedBy = "Terraform" })
}

resource "aws_kms_key" "outbox" {
  lifecycle { prevent_destroy = true }
  description             = "Durable synthetic-event outbox encryption"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AccountIamDelegation"
      Effect    = "Allow"
      Principal = { AWS = "${local.prefix}:iam::${local.account}:root" }
      Action    = "kms:*"
      Resource  = "*"
    }]
  })
  tags = merge(var.tags, { Name = "${var.name_prefix}-outbox", ManagedBy = "Terraform" })
}
resource "aws_s3_bucket" "outbox" {
  lifecycle { prevent_destroy = true }
  bucket        = "${var.name_prefix}-outbox-${local.account}"
  force_destroy = false
  tags          = merge(var.tags, { Name = "${var.name_prefix}-outbox", ManagedBy = "Terraform" })
}
resource "aws_s3_bucket_public_access_block" "outbox" {
  bucket                  = aws_s3_bucket.outbox.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "outbox" {
  bucket = aws_s3_bucket.outbox.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_versioning" "outbox" {
  bucket = aws_s3_bucket.outbox.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "outbox" {
  bucket = aws_s3_bucket.outbox.id
  rule {
    bucket_key_enabled = true
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.outbox.arn
    }
  }
}
resource "aws_s3_bucket_policy" "outbox" {
  bucket = aws_s3_bucket.outbox.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "DenyInsecureTransport"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:*"
        Resource  = [aws_s3_bucket.outbox.arn, "${aws_s3_bucket.outbox.arn}/*"]
        Condition = { Bool = { "aws:SecureTransport" = "false" } }
      },
      {
        Sid       = "RequireCreateOnlyWrites"
        Effect    = "Deny"
        Principal = "*"
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.outbox.arn}/readings/v1/*"
        Condition = { Null = { "s3:if-none-match" = "true" } }
      }
    ]
  })
}
resource "aws_iam_role_policy" "outbox" {
  name = "durable-outbox"
  role = aws_iam_role.task["generator"].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.outbox.arn
        Condition = { StringLike = { "s3:prefix" = "readings/v1/*" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.outbox.arn}/readings/v1/*"
      },
      {
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.outbox.arn
        Condition = {
          StringEquals = { "kms:ViaService" = "s3.${var.aws_region}.amazonaws.com" }
          ArnLike      = { "kms:EncryptionContext:aws:s3:arn" = [aws_s3_bucket.outbox.arn, "${aws_s3_bucket.outbox.arn}/*"] }
        }
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "generator" {
  name              = "/ecs/${var.name_prefix}-generator"
  retention_in_days = 7
  kms_key_id        = aws_kms_key.logs.arn
  tags              = merge(var.tags, { Name = "${var.name_prefix}-generator", ManagedBy = "Terraform" })
}
resource "aws_ecs_task_definition" "generator" {
  skip_destroy = true
  lifecycle { create_before_destroy = true }
  count                    = var.generator_image_uri == null ? 0 : 1
  family                   = "${var.name_prefix}-generator"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution["generator"].arn
  task_role_arn            = aws_iam_role.task["generator"].arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name            = "generator", image = var.generator_image_uri, essential = true
    user            = "10001:10001", readonlyRootFilesystem = true, stopTimeout = 120
    linuxParameters = { capabilities = { drop = ["ALL"] } }
    command         = ["python", "-m", "ingestion.producer", "--cycles", "1"]
    environment = [
      { name = "AWS_REGION", value = var.aws_region },
      { name = "SQS_QUEUE_NAME", value = "${var.name_prefix}-readings" },
      { name = "S3_OUTBOX_BUCKET", value = aws_s3_bucket.outbox.id },
      { name = "S3_OUTBOX_KMS_KEY_ARN", value = aws_kms_key.outbox.arn }
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.generator.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "generator"
        mode                  = "non-blocking"
        max-buffer-size       = "4m"
      }
    }
  }])
  tags = merge(var.tags, { Name = "${var.name_prefix}-generator", ManagedBy = "Terraform" })
}
