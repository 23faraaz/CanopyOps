output "cluster_arn" {
  description = "cluster arn (metadata only)"
  value       = aws_ecs_cluster.this.arn
}

output "log_kms_key_arn" {
  description = "log kms key arn (metadata only)"
  value       = aws_kms_key.logs.arn
}

output "alb_log_bucket_name" {
  description = "alb log bucket name (metadata only)"
  value       = aws_s3_bucket.alb_logs.id
}

output "execution_role_arns" {
  description = "execution role arns (metadata only)"
  value       = { for name, role in aws_iam_role.execution : name => role.arn }
}

output "task_role_arns" {
  description = "task role arns (metadata only)"
  value       = { for name, role in aws_iam_role.task : name => role.arn }
}

output "runtime_secret_arns" {
  description = "runtime secret arns (metadata only)"
  value       = { for name, secret in aws_secretsmanager_secret.runtime : name => secret.arn }
}

output "log_group_arns" {
  description = "log group arns (metadata only)"
  value       = local.log_arns
}

output "redis_user_group_id" {
  description = "redis user group id (metadata only)"
  value       = try(aws_elasticache_user_group.analytics[0].id, null)
}

output "outbox_bucket_name" {
  description = "Retained durable producer outbox bucket"
  value       = aws_s3_bucket.outbox.id
}
output "outbox_bucket_arn" {
  description = "Durable producer outbox bucket ARN"
  value       = aws_s3_bucket.outbox.arn
}
output "outbox_kms_key_arn" {
  description = "Public identifier of the outbox encryption key"
  value       = aws_kms_key.outbox.arn
}

output "generator_task_definition_arn" {
  description = "Finite manual generator task definition; this output does not run it"
  value       = try(aws_ecs_task_definition.generator[0].arn, null)
}
