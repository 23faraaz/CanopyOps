locals {
  terraform_ci = var.enable_terraform_workflows ? {
    plan    = { environment = "terraform-plan", write_state = false }
    deploy  = { environment = "terraform-deploy", write_state = true }
    destroy = { environment = "terraform-destroy", write_state = true }
  } : {}
  terraform_plan_prefix = "canopyops/demo/plans/"
  ci_network_statements = jsondecode(aws_iam_role_policy.demo_network.policy).Statement
  ci_read_statements    = [for statement in local.ci_network_statements : statement if statement.Sid == "ReadNetworkConfiguration"]
  ci_destroy_actions = [
    "ec2:DeleteVpc", "ec2:DeleteSubnet", "ec2:DeleteInternetGateway",
    "ec2:DeleteRouteTable", "ec2:DeleteRoute", "ec2:DetachInternetGateway",
    "ec2:DisassociateRouteTable"
  ]
  ci_destroy_statements = [
    for statement in local.ci_network_statements : merge(statement, {
      Action = [for action in flatten([statement.Action]) : action if contains(local.ci_destroy_actions, action)]
    }) if anytrue([for action in flatten([statement.Action]) : contains(local.ci_destroy_actions, action)])
  ]
}

resource "aws_iam_role" "terraform_ci" {
  for_each             = local.terraform_ci
  name                 = "${local.registry_prefix}-terraform-${each.key}"
  max_session_duration = 3600
  tags                 = { Name = "${local.registry_prefix}-terraform-${each.key}" }
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:environment:${each.value.environment}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "terraform_ci_network" {
  for_each = local.terraform_ci
  name     = "demo-network-${each.key}"
  role     = aws_iam_role.terraform_ci[each.key].id
  policy = each.key == "plan" ? jsonencode({
    Version = "2012-10-17", Statement = local.ci_read_statements
    }) : (each.key == "deploy" ? aws_iam_role_policy.demo_network.policy : jsonencode({
      Version = "2012-10-17", Statement = concat(local.ci_read_statements, local.ci_destroy_statements)
  }))
}

resource "aws_iam_role_policy" "terraform_ci_state" {
  for_each = local.terraform_ci
  name     = "demo-state-${each.key}"
  role     = aws_iam_role.terraform_ci[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "ListOnlyDemoPrefix"
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.terraform_state.arn
        Condition = { StringLike = { "s3:prefix" = "canopyops/demo/*" } }
      },
      {
        Sid      = "DemoStateObject"
        Effect   = "Allow"
        Action   = concat(["s3:GetObject"], each.value.write_state ? ["s3:PutObject"] : [])
        Resource = "${aws_s3_bucket.terraform_state.arn}/canopyops/demo/terraform.tfstate"
      },
      {
        Sid      = "DemoStateLockOnly"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.terraform_state.arn}/canopyops/demo/terraform.tfstate.tflock"
      }
    ]
  })
}

resource "aws_iam_role_policy" "terraform_ci_plans" {
  for_each = local.terraform_ci
  name     = "private-saved-plans-${each.key}"
  role     = aws_iam_role.terraform_ci[each.key].id
  policy = each.key == "plan" ? jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["s3:PutObject"]
      Resource  = "${aws_s3_bucket.terraform_state.arn}/${local.terraform_plan_prefix}*"
      Condition = { StringEquals = { "s3:x-amz-server-side-encryption" = "AES256" } }
    }]
    }) : jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObjectVersion"]
      Resource = "${aws_s3_bucket.terraform_state.arn}/${local.terraform_plan_prefix}*"
    }]
  })
}

resource "aws_s3_bucket_lifecycle_configuration" "terraform_plans" {
  count  = var.enable_terraform_workflows ? 1 : 0
  bucket = aws_s3_bucket.terraform_state.id
  rule {
    id     = "expire-demo-plans-only"
    status = "Enabled"
    filter { prefix = local.terraform_plan_prefix }
    expiration { days = 1 }
    noncurrent_version_expiration { noncurrent_days = 1 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  rule {
    id     = "remove-expired-plan-markers-only"
    status = "Enabled"
    filter { prefix = local.terraform_plan_prefix }
    expiration { expired_object_delete_marker = true }
  }
}
