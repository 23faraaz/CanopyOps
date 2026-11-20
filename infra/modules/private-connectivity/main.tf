locals {
  ecr_statements = [
    {
      Effect    = "Allow"
      Principal = { AWS = sort(tolist(var.execution_role_arns)) }
      Action    = ["ecr:GetAuthorizationToken"]
      Resource  = "*"
    },
    {
      Effect    = "Allow"
      Principal = { AWS = sort(tolist(var.execution_role_arns)) }
      Action    = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"]
      Resource  = sort(tolist(var.repository_arns))
    }
  ]
  interface_policies = {
    "ecr.api" = jsonencode({ Version = "2012-10-17", Statement = local.ecr_statements })
    "ecr.dkr" = jsonencode({ Version = "2012-10-17", Statement = local.ecr_statements })
    logs = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect    = "Allow"
        Principal = { AWS = sort(tolist(var.execution_role_arns)) }
        Action    = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource  = [for arn in sort(tolist(var.log_group_arns)) : "${arn}:log-stream:*"]
      }]
    })
    secretsmanager = jsonencode({
      Version = "2012-10-17"
      Statement = [for key, grant in var.secret_grants : {
        Sid       = "RuntimeSecret${substr(sha256(key), 0, 12)}"
        Effect    = "Allow"
        Principal = { AWS = grant.role_arn }
        Action    = ["secretsmanager:GetSecretValue"]
        Resource  = sort(tolist(grant.secret_arns))
      }]
    })
    sqs = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect    = "Allow"
          Principal = { AWS = var.producer_role_arn }
          Action    = ["sqs:SendMessage", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
          Resource  = var.queue_arn
        },
        {
          Effect    = "Allow"
          Principal = { AWS = var.worker_role_arn }
          Action    = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueUrl", "sqs:GetQueueAttributes"]
          Resource  = var.queue_arn
        }
      ]
    })
  }
}

data "aws_partition" "current" {}

resource "aws_vpc_endpoint" "interface" {
  for_each = local.interface_policies

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.aws_region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = var.app_subnet_ids
  security_group_ids  = [var.endpoint_security_group_id]
  policy              = each.value

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-${replace(each.key, ".", "-")}"
    ManagedBy = "Terraform"
  })
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = var.app_route_table_ids

  # AWS-owned ECR layer downloads use presigned requests. Restrict the
  # resource and action rather than assuming the task role is the signer.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([{
      Sid       = "ReadRegionalEcrLayersOnly"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "arn:${data.aws_partition.current.partition}:s3:::prod-${var.aws_region}-starport-layer-bucket/*"
      }], jsondecode(var.outbox_access == null ? "[]" : jsonencode([
        {
          Sid       = "ListProducerOutbox"
          Effect    = "Allow"
          Principal = { AWS = var.outbox_access.producer_role_arn }
          Action    = ["s3:ListBucket"]
          Resource  = var.outbox_access.bucket_arn
          Condition = { StringLike = { "s3:prefix" = "readings/v1/*" } }
        },
        {
          Sid       = "PersistAndReplayProducerOutbox"
          Effect    = "Allow"
          Principal = { AWS = var.outbox_access.producer_role_arn }
          Action    = ["s3:GetObject", "s3:PutObject"]
          Resource  = "${var.outbox_access.bucket_arn}/readings/v1/*"
        }
    ])))
  })

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-s3-image-layers"
    ManagedBy = "Terraform"
  })
}

resource "aws_vpc_security_group_egress_rule" "task_image_layers" {
  for_each = var.task_security_group_ids

  security_group_id = each.value
  description       = "HTTPS to regional S3 image layers; endpoint policy limits bucket access"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-${each.key}-s3-image-layers"
    ManagedBy = "Terraform"
  })
}
