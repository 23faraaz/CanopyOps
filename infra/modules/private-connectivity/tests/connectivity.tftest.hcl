mock_provider "aws" {
  override_during = plan
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}
override_resource {
  override_during = plan
  target          = aws_vpc_endpoint.s3
  values          = { prefix_list_id = "pl-11111111" }
}
variables {
  name_prefix                = "canopyops-test"
  aws_region                 = "eu-west-2"
  vpc_id                     = "vpc-11111111"
  app_subnet_ids             = ["subnet-11111111", "subnet-22222222"]
  app_route_table_ids        = ["rtb-11111111"]
  endpoint_security_group_id = "sg-11111111"
  task_security_group_ids = {
    api = "sg-22222222", analytics = "sg-33333333", worker = "sg-44444444", generator = "sg-55555555"
  }
  execution_role_arns = ["arn:aws:iam::123456789012:role/execution"]
  repository_arns = [
    "arn:aws:ecr:eu-west-2:123456789012:repository/test-api",
    "arn:aws:ecr:eu-west-2:123456789012:repository/test-worker",
    "arn:aws:ecr:eu-west-2:123456789012:repository/test-analytics"
  ]
  log_group_arns = ["arn:aws:logs:eu-west-2:123456789012:log-group:/test/api"]
  secret_grants = {
    api = {
      role_arn    = "arn:aws:iam::123456789012:role/execution"
      secret_arns = ["arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-api-ABC123"]
    }
  }
  producer_role_arn = "arn:aws:iam::123456789012:role/generator"
  worker_role_arn   = "arn:aws:iam::123456789012:role/worker"
  queue_arn         = "arn:aws:sqs:eu-west-2:123456789012:test-ingestion"
}
run "private_connectivity_contract" {
  command = plan
  assert {
    condition = length(aws_vpc_endpoint.interface) == 5 && alltrue([
      for endpoint in values(aws_vpc_endpoint.interface) : endpoint.private_dns_enabled && endpoint.vpc_endpoint_type == "Interface" && endpoint.security_group_ids == toset([var.endpoint_security_group_id])
    ])
    error_message = "All five interface endpoints must use private DNS and the endpoint group."
  }
  assert {
    condition     = aws_vpc_endpoint.s3.vpc_endpoint_type == "Gateway" && aws_vpc_endpoint.s3.route_table_ids == var.app_route_table_ids && jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Action == "s3:GetObject" && jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Resource == "arn:aws:s3:::prod-eu-west-2-starport-layer-bucket/*"
    error_message = "S3 access must be read-only regional ECR layers on app routes."
  }
  assert {
    condition = length(aws_vpc_security_group_egress_rule.task_image_layers) == 4 && alltrue([
      for rule in values(aws_vpc_security_group_egress_rule.task_image_layers) : rule.prefix_list_id == "pl-11111111" && rule.from_port == 443 && rule.to_port == 443 && rule.ip_protocol == "tcp" && rule.cidr_ipv4 == null && rule.cidr_ipv6 == null
    ])
    error_message = "Task egress must use HTTPS and the managed prefix list, never a public CIDR."
  }
  assert {
    condition     = jsondecode(aws_vpc_endpoint.interface["sqs"].policy).Statement[0].Principal.AWS == var.producer_role_arn && !contains(jsondecode(aws_vpc_endpoint.interface["sqs"].policy).Statement[0].Action, "sqs:DeleteMessage") && jsondecode(aws_vpc_endpoint.interface["sqs"].policy).Statement[1].Principal.AWS == var.worker_role_arn && !contains(jsondecode(aws_vpc_endpoint.interface["sqs"].policy).Statement[1].Action, "sqs:SendMessage")
    error_message = "Queue producer and consumer permissions must remain separate."
  }
}
run "reject_wildcard_repository" {
  command = plan
  variables {
    repository_arns = ["arn:aws:ecr:eu-west-2:123456789012:repository/*"]
  }
  expect_failures = [var.repository_arns]
}
run "reject_database_egress" {
  command = plan
  variables {
    task_security_group_ids = { postgres = "sg-99999999" }
  }
  expect_failures = [var.task_security_group_ids]
}
run "reject_untrusted_secret_role" {
  command = plan
  variables {
    secret_grants = {
      bad = {
        role_arn    = "arn:aws:iam::123456789012:role/unrelated"
        secret_arns = ["arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-api-ABC123"]
      }
    }
  }
  expect_failures = [var.secret_grants]
}

run "optional_outbox_is_producer_only" {
  command = plan
  variables {
    outbox_access = {
      bucket_arn        = "arn:aws:s3:::fictional-outbox"
      producer_role_arn = "arn:aws:iam::123456789012:role/generator"
    }
  }
  assert {
    condition     = length(jsondecode(aws_vpc_endpoint.s3.policy).Statement) == 3 && jsondecode(aws_vpc_endpoint.s3.policy).Statement[2].Principal.AWS == var.producer_role_arn && jsondecode(aws_vpc_endpoint.s3.policy).Statement[2].Resource == "arn:aws:s3:::fictional-outbox/readings/v1/*" && !contains(jsondecode(aws_vpc_endpoint.s3.policy).Statement[2].Action, "s3:DeleteObject")
    error_message = "Outbox access must not allow deletes or grant other principals access."
  }
}
