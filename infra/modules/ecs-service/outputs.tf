output "service" {
  description = "Service/task identifiers, private listener port and owned log group"
  value = {
    name                = aws_ecs_service.this.name, id = aws_ecs_service.this.id
    task_definition_arn = aws_ecs_task_definition.this.arn, log_group_name = aws_cloudwatch_log_group.this.name
    container_name      = var.service_name, container_port = local.port
  }
}
