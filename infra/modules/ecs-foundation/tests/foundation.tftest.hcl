mock_provider "aws" {
  override_during = plan
  mock_resource "aws_secretsmanager_secret" { defaults = { arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-ABC123" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws" } }
}
override_resource {
  override_during = plan
  target          = aws_iam_role.execution["generator"]
  values          = { arn = "arn:aws:iam::123456789012:role/generator-execution" }
}
override_resource {
  override_during = plan
  target          = aws_iam_role.task["generator"]
  values          = { arn = "arn:aws:iam::123456789012:role/generator-task" }
}
override_resource {
  override_during = plan
  target          = aws_s3_bucket.outbox
  values          = { id = "fictional-outbox", arn = "arn:aws:s3:::fictional-outbox" }
}
override_resource {
  override_during = plan
  target          = aws_kms_key.outbox
  values          = { arn = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111" }
}
variables {
  name_prefix              = "canopyops-test"
  permissions_boundary_arn = "arn:aws:iam::123456789012:policy/canopyops-test-runtime-boundary"
  aws_region               = "eu-west-2"
  repository_arns = {
    api       = "arn:aws:ecr:eu-west-2:123456789012:repository/api"
    worker    = "arn:aws:ecr:eu-west-2:123456789012:repository/worker"
    analytics = "arn:aws:ecr:eu-west-2:123456789012:repository/analytics"
  }
  queue_arn      = "arn:aws:sqs:eu-west-2:123456789012:test-ingestion"
  redis_user_ids = ["test-analytics", "test-disabled-default"]
  tags           = { Project = "canopyops", Environment = "test", ManagedBy = "Terraform" }
}
run "scoped_runtime_roles" {
  command = plan
  assert {
    condition = length(aws_iam_role.execution) == 4 && length(aws_iam_role.task) == 4 && alltrue([
      for name, role in aws_iam_role.task : role.name != aws_iam_role.execution[name].name && role.permissions_boundary == var.permissions_boundary_arn && aws_iam_role.execution[name].permissions_boundary == var.permissions_boundary_arn && jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
    ])
    error_message = "Execution and application identities must remain separate and account-scoped."
  }
  assert {
    condition     = alltrue([for name, policy in aws_iam_role_policy.execution : jsondecode(policy.policy).Statement[1].Resource == var.repository_arns[name == "generator" ? "worker" : name]])
    error_message = "Execution roles must pull only their own repository."
  }
  assert {
    condition     = !contains(jsondecode(aws_iam_role_policy.queue["generator"].policy).Statement[0].Action, "sqs:DeleteMessage") && !contains(jsondecode(aws_iam_role_policy.queue["worker"].policy).Statement[0].Action, "sqs:SendMessage")
    error_message = "Producer and worker queue responsibilities must remain separate."
  }
  assert {
    condition     = aws_s3_bucket.alb_logs.force_destroy == false && aws_s3_bucket_public_access_block.alb_logs.block_public_policy && aws_kms_key.logs.enable_key_rotation && length(aws_secretsmanager_secret.runtime) == 4
    error_message = "Retain logs safely and create only secret metadata."
  }
}
run "reject_wildcard_repo" {
  command = plan
  variables { repository_arns = { api = "*", worker = "*", analytics = "*" } }
  expect_failures = [var.repository_arns]
}
run "reject_missing_disabled_redis_user" {
  command = plan
  variables { redis_user_ids = ["analytics"] }
  expect_failures = [var.redis_user_ids]
}

run "finite_durable_generator" {
  command = plan
  variables {
    generator_image_uri = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
  assert {
    condition     = length(aws_ecs_task_definition.generator) == 1 && jsondecode(aws_ecs_task_definition.generator[0].container_definitions)[0].command == ["python", "-m", "ingestion.producer", "--cycles", "1"] && jsondecode(aws_ecs_task_definition.generator[0].container_definitions)[0].readonlyRootFilesystem && jsondecode(aws_ecs_task_definition.generator[0].container_definitions)[0].user == "10001:10001"
    error_message = "Generator must be finite, non-root and use a read-only container filesystem."
  }
}

run "metadata_before_redis_credentials" {
  command = plan
  variables { redis_user_ids = [] }
  assert {
    condition     = length(aws_elasticache_user_group.analytics) == 0 && length(aws_secretsmanager_secret.runtime) == 4
    error_message = "Prepare secret metadata before external credential/user provisioning; do not invent passwords in Terraform."
  }
}
