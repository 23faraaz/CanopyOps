locals {
  name  = "${var.name_prefix}-${var.service_name}"
  port  = { api = 8000, analytics = 8081, worker = 9100 }[var.service_name]
  path  = var.service_name == "worker" ? "/metrics" : "/health/live"
  ports = var.service_name == "worker" ? { metrics = 9100 } : { http = local.port, metrics = 9100 }
  tags  = merge(var.tags, { ManagedBy = "Terraform", Service = var.service_name })
  environment = merge(var.environment_variables, {
    HOST         = "0.0.0.0", PORT = tostring(local.port), BACKEND = "postgres"
    METRICS_HOST = "0.0.0.0", METRICS_PORT = "9100"
  })
}
resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_key_arn
  tags              = merge(local.tags, { Name = local.name })
}
resource "aws_ecs_task_definition" "this" {
  family                   = local.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(var.cpu)
  memory                   = tostring(var.memory)
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name            = var.service_name, image = var.image_uri, essential = true
    user            = "10001:10001", readonlyRootFilesystem = true, stopTimeout = 120
    linuxParameters = { capabilities = { drop = ["ALL"] } }
    portMappings = [for name, port in local.ports : {
      name = name, containerPort = port, hostPort = port, protocol = "tcp"
    }]
    environment = [for name, value in local.environment : { name = name, value = value }]
    secrets     = [for name, reference in var.secrets : { name = name, valueFrom = reference }]
    healthCheck = {
      command  = ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:${local.port}${local.path}',timeout=3)"]
      interval = 30, timeout = 5, retries = 3, startPeriod = 30
    }
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.this.name, awslogs-region = var.aws_region
        awslogs-stream-prefix = var.service_name, mode = "non-blocking", max-buffer-size = "4m"
      }
    }
  }])
  tags = merge(local.tags, { Name = local.name })
}
resource "aws_ecs_service" "this" {
  name                               = local.name
  cluster                            = var.cluster_arn
  task_definition                    = aws_ecs_task_definition.this.arn
  launch_type                        = "FARGATE"
  platform_version                   = "1.4.0"
  desired_count                      = var.desired_count
  enable_execute_command             = false
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  availability_zone_rebalancing      = "ENABLED"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  wait_for_steady_state              = true
  health_check_grace_period_seconds  = var.load_balancer == null ? null : 60
  deployment_controller { type = "ECS" }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = var.load_balancer == null ? {} : { http = var.load_balancer }
    content {
      target_group_arn = load_balancer.value.target_group_arn
      container_name   = var.service_name
      container_port   = local.port
    }
  }
  tags = merge(local.tags, { Name = local.name })
  timeouts {
    create = "20m"
    update = "20m"
    delete = "20m"
  }
}
