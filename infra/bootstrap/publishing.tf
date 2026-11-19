locals {
  registry_prefix = "${var.project_name}-${var.environment}"
  registry_arns = [
    for service in ["api", "worker", "analytics"] :
    "arn:${data.aws_partition.current.partition}:ecr:${var.aws_region}:${data.aws_caller_identity.current.account_id}:repository/${local.registry_prefix}/${service}"
  ]
  github_oidc_arn = var.existing_github_oidc_provider_arn != null ? var.existing_github_oidc_provider_arn : try(aws_iam_openid_connect_provider.github[0].arn, null)
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = (var.enable_github_publishing || var.enable_terraform_workflows) && var.existing_github_oidc_provider_arn == null ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
  tags           = { Name = "${local.registry_prefix}-github-oidc" }
}

resource "aws_iam_role" "github_publisher" {
  count                = var.enable_github_publishing ? 1 : 0
  name                 = "${local.registry_prefix}-github-publisher"
  max_session_duration = 3600
  tags                 = { Name = "${local.registry_prefix}-github-publisher" }
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = local.github_oidc_arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix}:environment:${var.github_publish_environment}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "github_publisher" {
  count = var.enable_github_publishing ? 1 : 0
  name  = "publish-three-private-images"
  role  = aws_iam_role.github_publisher[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "RegistryLogin"
        Effect    = "Allow"
        Action    = ["ecr:GetAuthorizationToken"]
        Resource  = "*"
        Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
      },
      {
        Sid    = "PushAndVerifyOnlyTheseRepositories"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
          "ecr:BatchGetImage", "ecr:DescribeRepositories", "ecr:DescribeImageScanFindings"
        ]
        Resource  = local.registry_arns
        Condition = { StringEquals = { "aws:RequestedRegion" = var.aws_region } }
      }
    ]
  })
}

resource "aws_iam_role_policy" "registry_state" {
  name = "registry-state-access"
  role = aws_iam_role.terraform_demo.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["s3:ListBucket"]
        Resource  = aws_s3_bucket.terraform_state.arn
        Condition = { StringLike = { "s3:prefix" = "canopyops/registry/*" } }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${aws_s3_bucket.terraform_state.arn}/canopyops/registry/terraform.tfstate"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${aws_s3_bucket.terraform_state.arn}/canopyops/registry/terraform.tfstate.tflock"
      }
    ]
  })
}

resource "aws_iam_policy" "registry_management" {
  name = "${local.registry_prefix}-registry-management"
  tags = { Name = "${local.registry_prefix}-registry-management" }
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "CreateThreeTaggedRepositories"
        Effect   = "Allow"
        Action   = ["ecr:CreateRepository", "ecr:TagResource"]
        Resource = local.registry_arns
        Condition = {
          StringEquals = {
            "aws:RequestedRegion"        = var.aws_region
            "aws:RequestTag/Project"     = var.project_name
            "aws:RequestTag/Environment" = var.environment
            "aws:RequestTag/ManagedBy"   = "Terraform"
          }
          "ForAllValues:StringEquals" = { "aws:TagKeys" = ["Name", "Service", "Project", "Environment", "ManagedBy"] }
        }
      },
      {
        Sid    = "ManageOnlyOwnedRepositories"
        Effect = "Allow"
        Action = [
          "ecr:DescribeRepositories", "ecr:ListTagsForResource",
          "ecr:DescribeImages", "ecr:StartLifecyclePolicyPreview", "ecr:GetLifecyclePolicyPreview",
          "ecr:GetLifecyclePolicy", "ecr:PutLifecyclePolicy", "ecr:DeleteLifecyclePolicy",
          "ecr:PutImageTagMutability", "ecr:PutImageScanningConfiguration", "ecr:DeleteRepository"
        ]
        Resource = local.registry_arns
        Condition = {
          StringEquals = {
            "aws:RequestedRegion"         = var.aws_region
            "aws:ResourceTag/Project"     = var.project_name
            "aws:ResourceTag/Environment" = var.environment
            "aws:ResourceTag/ManagedBy"   = "Terraform"
          }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "registry_management" {
  role       = aws_iam_role.terraform_demo.name
  policy_arn = aws_iam_policy.registry_management.arn
}
