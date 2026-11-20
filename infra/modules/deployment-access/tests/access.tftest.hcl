mock_provider "aws" { override_during = plan }
variables {
  name_prefix    = "canopyops-demo"
  account_id     = "123456789012"
  aws_region     = "eu-west-2"
  project_name   = "canopyops"
  environment    = "demo"
  role_name      = "canopyops-demo-terraform-plan"
  mode           = "plan"
  domain_name    = "demo.example.test"
  hosted_zone_id = "ZTEST123"
  boundary_arn   = "arn:aws:iam::123456789012:policy/canopyops-demo-runtime-boundary"
  tags           = { Project = "canopyops", Environment = "demo", ManagedBy = "Terraform" }
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
