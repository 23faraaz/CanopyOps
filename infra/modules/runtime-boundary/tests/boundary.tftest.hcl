mock_provider "aws" { override_during = plan }
variables {
  name_prefix = "canopyops-demo"
  aws_region  = "eu-west-2"
  account_id  = "123456789012"
}
run "runtime_maximum_is_scoped" {
  command = plan
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_policy.runtime_boundary.policy).Statement : alltrue([for action in statement.Action : !startswith(action, "iam:") && !startswith(action, "sts:") && action != "s3:DeleteObject" && action != "secretsmanager:PutSecretValue" && action != "*"])])
    error_message = "Runtime identities must not administer IAM, assume roles, delete events or change secrets."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.runtime_boundary.policy).Statement[0].Condition.StringEquals["aws:RequestedRegion"] == "eu-west-2" && jsondecode(aws_iam_policy.runtime_boundary.policy).Statement[4].Resource == "arn:aws:sqs:eu-west-2:123456789012:canopyops-demo-readings" && jsondecode(aws_iam_policy.runtime_boundary.policy).Statement[6].Resource == "arn:aws:s3:::canopyops-demo-outbox-123456789012/readings/v1/*"
    error_message = "Queue and outbox maximum access must remain account, region and prefix scoped."
  }
}
run "reject_invalid_account" {
  command = plan
  variables { account_id = "*" }
  expect_failures = [var.account_id]
}
