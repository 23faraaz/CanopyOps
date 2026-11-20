mock_provider "aws" { override_during = plan }
override_module {
  target = module.network
  outputs = {
    vpc_id                   = "vpc-11111111"
    public_subnet_ids        = { a = "subnet-11111111", b = "subnet-22222222" }
    private_app_subnet_ids   = { a = "subnet-33333333", b = "subnet-44444444" }
    isolated_data_subnet_ids = { a = "subnet-55555555", b = "subnet-66666666" }
    route_table_ids          = { app = "rtb-11111111" }
  }
}
override_module {
  target = module.foundation["demo"]
  outputs = {
    cluster_arn         = "arn:aws:ecs:eu-west-2:123456789012:cluster/test"
    log_kms_key_arn     = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
    alb_log_bucket_name = "fictional-alb-logs"
    redis_user_group_id = "fictional-analytics"
    outbox_bucket_name  = "fictional-outbox"
    outbox_bucket_arn   = "arn:aws:s3:::fictional-outbox"
    execution_role_arns = {
      api       = "arn:aws:iam::123456789012:role/api-execution"
      worker    = "arn:aws:iam::123456789012:role/worker-execution"
      analytics = "arn:aws:iam::123456789012:role/analytics-execution"
      generator = "arn:aws:iam::123456789012:role/generator-execution"
    }
    task_role_arns = {
      api       = "arn:aws:iam::123456789012:role/api-task"
      worker    = "arn:aws:iam::123456789012:role/worker-task"
      analytics = "arn:aws:iam::123456789012:role/analytics-task"
      generator = "arn:aws:iam::123456789012:role/generator-task"
    }
    runtime_secret_arns = {
      api       = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-api-ABC123"
      worker    = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-worker-ABC123"
      analytics = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-analytics-ABC123"
      redis     = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:test-redis-ABC123"
    }
    log_group_arns = { api = "arn:aws:logs:eu-west-2:123456789012:log-group:/ecs/test-api" }
  }
}
override_module {
  target = module.ingress["demo"]
  outputs = {
    target_group_arns = { api = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:targetgroup/api/1111111111111111", analytics = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:targetgroup/analytics/1111111111111111" }
    urls              = { api = "https://demo.example.test", analytics = "https://analytics.demo.example.test" }
  }
}
variables {
  project_name               = "canopyops"
  environment                = "demo"
  aws_region                 = "eu-west-2"
  availability_zones         = ["eu-west-2a", "eu-west-2b"]
  vpc_cidr                   = "10.42.0.0/16"
  public_subnet_cidrs        = ["10.42.0.0/24", "10.42.1.0/24"]
  private_app_subnet_cidrs   = ["10.42.10.0/24", "10.42.11.0/24"]
  isolated_data_subnet_cidrs = ["10.42.20.0/24", "10.42.21.0/24"]
  application = {
    permissions_boundary_arn  = "arn:aws:iam::123456789012:policy/canopyops-demo-runtime-boundary"
    domain_name               = "demo.example.test"
    hosted_zone_id            = "Z1111111111111"
    postgres_version          = "17.8"
    redis_version             = "7.1"
    final_snapshot_identifier = "canopyops-demo-final-test"
    redis_user_ids            = ["fictional-analytics", "fictional-disabled-default"]
    repository_arns = {
      api       = "arn:aws:ecr:eu-west-2:123456789012:repository/api"
      worker    = "arn:aws:ecr:eu-west-2:123456789012:repository/worker"
      analytics = "arn:aws:ecr:eu-west-2:123456789012:repository/analytics"
    }
    image_uris = {
      api       = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      worker    = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/worker@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      analytics = "123456789012.dkr.ecr.eu-west-2.amazonaws.com/analytics@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
  }
}
run "prepared_services_are_stopped" {
  command = plan
  assert {
    condition     = length(module.service) == 3 && length(module.connectivity) == 1 && output.application_metadata.services_active == false
    error_message = "Prepared cloud services must remain stopped until migration and runtime secrets are verified."
  }
}

run "metadata_before_redis_users" {
  command = plan
  variables {
    application = merge(var.application, { redis_user_ids = [] })
  }
  assert {
    condition     = length(module.cache) == 0 && output.application_metadata.cache == null && output.application_metadata.services_active == false
    error_message = "Prepare metadata without an unconfigured cache or active tasks."
  }
}
