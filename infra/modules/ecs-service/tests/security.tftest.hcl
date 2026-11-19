mock_provider "aws" {
  override_during = plan
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-test" }
  }
  mock_resource "aws_ecs_task_definition" {
    defaults = { arn = "arn:aws:ecs:eu-west-2:123456789012:task-definition/canopyops-test-api:1" }
  }
}
variables {
  name_prefix        = "canopyops-test"
  tags               = { Project = "canopyops", Environment = "test" }
  subnet_ids         = ["subnet-11111111", "subnet-22222222"]
  security_group_ids = ["sg-11111111"]
  log_kms_key_arn    = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
  service_name       = "api"
  aws_region         = "eu-west-2"
  cluster_arn        = "arn:aws:ecs:eu-west-2:123456789012:cluster/canopyops-test"
  execution_role_arn = "arn:aws:iam::123456789012:role/fixture-execution"
  task_role_arn      = "arn:aws:iam::123456789012:role/fixture-task"
  image_uri          = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/canopyops-test/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
run "private_nonroot_digest_pinned_service" {
  command = plan
  assert {
    condition     = jsondecode(aws_ecs_task_definition.this.container_definitions)[0].user == "10001:10001" && jsondecode(aws_ecs_task_definition.this.container_definitions)[0].readonlyRootFilesystem && jsondecode(aws_ecs_task_definition.this.container_definitions)[0].stopTimeout == 120 && one(aws_ecs_service.this.network_configuration).assign_public_ip == false
    error_message = "Keep tasks non-root/private/read-only with bounded graceful shutdown."
  }
  assert {
    condition     = one(aws_ecs_service.this.deployment_circuit_breaker).rollback && one(aws_ecs_service.this.deployment_circuit_breaker).enable && aws_ecs_service.this.deployment_minimum_healthy_percent == 100 && aws_ecs_service.this.deployment_maximum_percent == 200 && aws_ecs_service.this.wait_for_steady_state
    error_message = "Rolling overlap and automatic failure recovery must stay enabled; live availability still needs measurement."
  }
  assert {
    condition     = !contains(keys(jsondecode(aws_ecs_task_definition.this.container_definitions)[0]), "command") && aws_ecs_service.this.task_definition == aws_ecs_task_definition.this.arn
    error_message = "Preserve the Dockerfile command and Terraform ownership of the desired task release."
  }
}
run "worker_is_not_publicly_routed" {
  command = plan
  variables { service_name = "worker" }
  assert {
    condition     = length(aws_ecs_service.this.load_balancer) == 0 && strcontains(jsondecode(aws_ecs_task_definition.this.container_definitions)[0].healthCheck.command[3], "9100/metrics")
    error_message = "Worker metrics are private process health, never an ALB application endpoint."
  }
}
run "analytics_target_matches_container_port" {
  command = plan
  variables {
    service_name  = "analytics"
    load_balancer = { target_group_arn = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:targetgroup/fixture/1111111111111111" }
  }
  assert {
    condition     = one(aws_ecs_service.this.load_balancer).container_port == 8081 && strcontains(jsondecode(aws_ecs_task_definition.this.container_definitions)[0].healthCheck.command[3], "8081/health/live")
    error_message = "Use the actual analytics listener, not its laptop port mapping."
  }
}
run "reject_mutable_tag" {
  command = plan
  variables { image_uri = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/canopyops-test/api:latest" }
  expect_failures = [var.image_uri]
}
run "reject_plaintext_credential_setting" {
  command = plan
  variables { environment_variables = { PGPASSWORD = "synthetic-invalid-fixture" } }
  expect_failures = [var.environment_variables]
}
run "reject_non_arn_secret" {
  command = plan
  variables { secrets = { PGPASSWORD = "synthetic-invalid-fixture" } }
  expect_failures = [var.secrets]
}
run "reject_shared_execution_task_identity" {
  command = plan
  variables { task_role_arn = "arn:aws:iam::123456789012:role/fixture-execution" }
  expect_failures = [var.task_role_arn]
}
run "reject_public_worker_target" {
  command = plan
  variables {
    service_name  = "worker"
    load_balancer = { target_group_arn = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:targetgroup/fixture/1111111111111111" }
  }
  expect_failures = [var.load_balancer]
}
run "reject_invalid_cpu_memory_pair" {
  command = plan
  variables { memory = 512 }
  expect_failures = [var.memory]
}

run "accept_runtime_reference_without_plaintext" {
  command = plan
  variables {
    environment_variables = { PGHOST = "postgres.example.test", PGSSLMODE = "verify-full" }
    secrets               = { PGPASSWORD = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:runtime-fixture-AbCdEf:password::" }
  }
  assert {
    condition     = one(jsondecode(aws_ecs_task_definition.this.container_definitions)[0].secrets).valueFrom == var.secrets["PGPASSWORD"] && alltrue([for setting in jsondecode(aws_ecs_task_definition.this.container_definitions)[0].environment : setting.name != "PGPASSWORD"])
    error_message = "Keep only the runtime secret reference in the task definition, never a password environment value."
  }
}
run "reject_secret_overriding_listener" {
  command = plan
  variables { secrets = { PORT = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:fixture-AbCdEf" } }
  expect_failures = [var.secrets]
}
