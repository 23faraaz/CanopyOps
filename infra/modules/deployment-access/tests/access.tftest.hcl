mock_provider "aws" { override_during = plan }
variables {
  name_prefix     = "canopyops-demo"
  account_id      = "123456789012"
  aws_region      = "eu-west-2"
  project_name    = "canopyops"
  environment     = "demo"
  role_name       = "canopyops-demo-terraform-plan"
  mode            = "plan"
  domain_name     = "demo.example.test"
  hosted_zone_id  = "ZTEST123"
  certificate_arn = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
  boundary_arn    = "arn:aws:iam::123456789012:policy/canopyops-demo-runtime-boundary"
  tags            = { Project = "canopyops", Environment = "demo", ManagedBy = "Terraform" }
}
run "plan_only_reads_and_protective_denies" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.this) == 3 && alltrue([for policy in values(aws_iam_policy.this) : alltrue([for row in jsondecode(policy.policy).Statement : row.Effect == "Deny" || alltrue([for action in row.Action : can(regex("^[^:]+:(Get|List|Describe)", action))])])])
    error_message = "Plan identity must have metadata reads and protective denies only."
  }
}
run "deploy_is_bounded_and_within_iam_quotas" {
  command = plan
  variables {
    mode      = "deploy"
    role_name = "canopyops-demo-terraform-deploy"
  }
  assert {
    condition     = length(aws_iam_policy.this) <= 10 && alltrue([for policy in values(aws_iam_policy.this) : length(policy.policy) <= 6144])
    error_message = "Managed policy size and role attachment quotas must be respected."
  }
  assert {
    condition     = alltrue([for row in jsondecode(aws_iam_policy.this["identity"].policy).Statement : !contains(row.Action, "iam:PassRole") || row.Condition.StringEquals["iam:PassedToService"] == "ecs-tasks.amazonaws.com"])
    error_message = "Role delegation must be limited to ECS tasks."
  }
  assert {
    condition     = alltrue([for row in jsondecode(aws_iam_policy.this["identity"].policy).Statement : !contains(row.Action, "iam:CreateRole") || row.Condition.StringEquals["iam:PermissionsBoundary"] == var.boundary_arn])
    error_message = "Every newly delegated role must carry the protected boundary."
  }
}
run "destroy_does_not_create_or_read_data" {
  command = plan
  variables {
    mode      = "destroy"
    role_name = "canopyops-demo-terraform-destroy"
  }
  assert {
    condition     = alltrue([for policy in values(aws_iam_policy.this) : alltrue([for row in jsondecode(policy.policy).Statement : row.Effect == "Deny" || alltrue([for action in row.Action : !contains(["s3:GetObject", "s3:DeleteObject", "iam:CreateRole", "ecs:CreateService", "kms:ScheduleKeyDeletion", "secretsmanager:GetSecretValue"], action)])])])
    error_message = "Destroy must not create resources, read credentials/data or delete retained key material."
  }
}
run "wrong_role_is_rejected" {
  command = plan
  variables { role_name = "Administrator" }
  expect_failures = [var.role_name]
}

run "deploy_preserves_bootstrap_and_foundation" {
  command = plan
  variables {
    mode      = "deploy"
    role_name = "canopyops-demo-terraform-deploy"
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : row.Effect == "Deny" || alltrue([
        for action in row.Action : !contains([
          "acm:RequestCertificate", "acm:DeleteCertificate", "acm:AddTagsToCertificate", "acm:RemoveTagsFromCertificate",
          "ecs:DeleteCluster", "iam:DeleteRole", "iam:DeleteRolePolicy", "secretsmanager:DeleteSecret",
          "kms:DeleteAlias", "kms:ScheduleKeyDeletion", "s3:DeleteBucket", "s3:DeleteObject", "s3:DeleteObjectVersion"
        ], action)
      ])
    ])])
    error_message = "Application identities must not mutate bootstrap certificates or delete retained foundation resources."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : !contains(row.Action, "acm:DescribeCertificate") || row.Resource == var.certificate_arn
    ])])
    error_message = "Certificate metadata reads must reference the exact bootstrap certificate."
  }
  assert {
    condition = alltrue([for row in jsondecode(aws_iam_policy.this["edge"].policy).Statement :
      !contains(row.Action, "route53:ChangeResourceRecordSets") || (
        row.Resource == "arn:aws:route53:::hostedzone/ZTEST123" &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"]) == toset(["demo.example.test", "analytics.demo.example.test"]), false) &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsRecordTypes"]) == toset(["A"]), false) &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsActions"]) == toset(["CREATE", "UPSERT", "DELETE"]), false)
      )
    ])
    error_message = "DNS writes must target only the two application A records with operation-specific actions, preserving validation CNAMEs and parent delegation."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : !contains(row.Action, "logs:DeleteLogGroup") || toset(row.Resource) == toset([
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-api",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-api:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-worker",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-worker:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-analytics",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-analytics:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/aws/rds/instance/canopyops-demo-postgres/*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-demo",
        "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-demo:*"
      ])
    ])])
    error_message = "Log deletion must exclude retained generator logs and wildcard component access."
  }
  assert {
    condition     = length(aws_iam_policy.this) <= 10 && alltrue([for policy in values(aws_iam_policy.this) : length(policy.policy) <= 6144])
    error_message = "Tightened permissions must still fit IAM managed-policy and attachment quotas."
  }
}

run "destroy_preserves_bootstrap_and_foundation" {
  command = plan
  variables {
    mode      = "destroy"
    role_name = "canopyops-demo-terraform-destroy"
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : row.Effect == "Deny" || alltrue([
        for action in row.Action : !contains([
          "acm:RequestCertificate", "acm:DeleteCertificate", "acm:AddTagsToCertificate", "acm:RemoveTagsFromCertificate",
          "ecs:DeleteCluster", "iam:DeleteRole", "iam:DeleteRolePolicy", "secretsmanager:DeleteSecret",
          "kms:DeleteAlias", "kms:ScheduleKeyDeletion", "s3:DeleteBucket", "s3:DeleteObject", "s3:DeleteObjectVersion"
        ], action)
      ])
    ])])
    error_message = "Application identities must not mutate bootstrap certificates or delete retained foundation resources."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : !contains(row.Action, "acm:DescribeCertificate") || row.Resource == var.certificate_arn
    ])])
    error_message = "Certificate metadata reads must reference the exact bootstrap certificate."
  }
  assert {
    condition = alltrue([for row in jsondecode(aws_iam_policy.this["edge"].policy).Statement :
      !contains(row.Action, "route53:ChangeResourceRecordSets") || (
        row.Resource == "arn:aws:route53:::hostedzone/ZTEST123" &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsNormalizedRecordNames"]) == toset(["demo.example.test", "analytics.demo.example.test"]), false) &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsRecordTypes"]) == toset(["A"]), false) &&
        try(toset(row.Condition["ForAllValues:StringEquals"]["route53:ChangeResourceRecordSetsActions"]) == toset(["DELETE"]), false)
      )
    ])
    error_message = "DNS writes must target only the two application A records with operation-specific actions, preserving validation CNAMEs and parent delegation."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_policy.this) : alltrue([
      for row in jsondecode(policy.policy).Statement : !contains(row.Action, "logs:DeleteLogGroup") || toset(row.Resource) == toset([
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-api",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-api:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-worker",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-worker:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-analytics",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/canopyops-demo-analytics:*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:/aws/rds/instance/canopyops-demo-postgres/*",
        "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-demo",
        "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-demo:*"
      ])
    ])])
    error_message = "Log deletion must exclude retained generator logs and wildcard component access."
  }
  assert {
    condition     = length(aws_iam_policy.this) <= 10 && alltrue([for policy in values(aws_iam_policy.this) : length(policy.policy) <= 6144])
    error_message = "Tightened permissions must still fit IAM managed-policy and attachment quotas."
  }
}

run "reject_certificate_from_another_account" {
  command = plan
  variables {
    certificate_arn = "arn:aws:acm:eu-west-2:999999999999:certificate/11111111-1111-1111-1111-111111111111"
  }
  expect_failures = [var.certificate_arn]
}
run "reject_certificate_from_another_region" {
  command = plan
  variables {
    certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/11111111-1111-1111-1111-111111111111"
  }
  expect_failures = [var.certificate_arn]
}


run "refresh_metadata_reads_remain_resource_scoped" {
  command = plan
  assert {
    condition = alltrue([
      for row in jsondecode(aws_iam_policy.this["read_bucket_metadata"].policy).Statement :
      !contains(row.Action, "s3:GetBucketLogging") || (
        row.Effect == "Allow" && toset(row.Resource) == toset([
          "arn:aws:s3:::canopyops-demo-alb-logs-123456789012",
          "arn:aws:s3:::canopyops-demo-outbox-123456789012"
        ])
      )
      ]) && anytrue([
      for row in jsondecode(aws_iam_policy.this["read_bucket_metadata"].policy).Statement : contains(row.Action, "s3:GetBucketLogging")
    ])
    error_message = "Bucket logging refresh must be permitted only on the two application buckets, never state or object resources."
  }
  assert {
    condition = alltrue([
      for row in jsondecode(aws_iam_policy.this["read_security"].policy).Statement :
      !contains(row.Action, "kms:GetKeyRotationStatus") || (
        row.Effect == "Allow" &&
        row.Resource == "arn:aws:kms:eu-west-2:123456789012:key/*" &&
        row.Condition.StringEquals == {
          "aws:RequestedRegion"         = "eu-west-2"
          "aws:ResourceTag/Project"     = "canopyops"
          "aws:ResourceTag/Environment" = "demo"
          "aws:ResourceTag/ManagedBy"   = "Terraform"
          "aws:ResourceTag/Name"        = ["canopyops-demo-logs", "canopyops-demo-outbox"]
        }
      )
      ]) && anytrue([
      for row in jsondecode(aws_iam_policy.this["read_security"].policy).Statement : contains(row.Action, "kms:GetKeyRotationStatus")
    ])
    error_message = "Rotation-status refresh must retain account/region and all ownership/key-name conditions."
  }
  assert {
    condition = alltrue([
      for group in ["read_security", "read_bucket_metadata"] : alltrue([
        for row in jsondecode(aws_iam_policy.this[group].policy).Statement : row.Effect == "Deny" || alltrue([
          for action in row.Action : !contains(["s3:GetObject", "s3:GetObjectVersion", "kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "secretsmanager:GetSecretValue"], action)
        ])
      ])
    ])
    error_message = "Refresh reads must not add object data, credential values or cryptographic access."
  }
}

run "base_network_deploy_requires_ownership" {
  command = plan
  variables {
    mode      = "deploy"
    role_name = "canopyops-demo-terraform-deploy"
  }
  assert {
    condition = alltrue([for row in jsondecode(aws_iam_policy.this["base_network"].policy).Statement :
      row.Resource != "*" && row.Condition.StringEquals["aws:RequestedRegion"] == "eu-west-2" &&
      (try(row.Condition.StringEquals["aws:RequestTag/Project"], "") == "canopyops" || try(row.Condition.StringEquals["aws:ResourceTag/Project"], "") == "canopyops")
    ])
    error_message = "Base networking must retain account/region resources and ownership conditions."
  }
  assert {
    condition = anytrue([for row in jsondecode(aws_iam_policy.this["base_network"].policy).Statement :
      contains(row.Action, "ec2:CreateVpc") && row.Resource == "arn:aws:ec2:eu-west-2:123456789012:vpc/*"
    ])
    error_message = "Deployment must authorize tagged VPC creation."
  }
}
run "base_network_destroy_has_no_creates" {
  command = plan
  variables {
    mode      = "destroy"
    role_name = "canopyops-demo-terraform-destroy"
  }
  assert {
    condition = alltrue([for row in jsondecode(aws_iam_policy.this["base_network"].policy).Statement :
      alltrue([for action in row.Action : !can(regex("^ec2:(Create|Modify|Attach|Replace|Associate)", action))])
    ])
    error_message = "Network teardown must not grant creation or configuration expansion."
  }
}
