variable "name_prefix" {
  description = "Project/environment resource name prefix"
  type        = string
}
variable "aws_region" {
  description = "Region containing the VPC and repositories"
  type        = string
}
variable "vpc_id" {
  description = "VPC with DNS support and DNS hostnames enabled"
  type        = string
}
variable "app_subnet_ids" {
  description = "Private application subnets in two distinct availability zones"
  type        = set(string)
  validation {
    condition     = length(var.app_subnet_ids) == 2
    error_message = "Provide two private application subnets."
  }
}
variable "app_route_table_ids" {
  description = "Application route tables only; excludes public and isolated data routes"
  type        = set(string)
  validation {
    condition     = length(var.app_route_table_ids) > 0
    error_message = "At least one application route table is required."
  }
}
variable "endpoint_security_group_id" {
  description = "Endpoint group allowing HTTPS only from the task security groups"
  type        = string
}
variable "task_security_group_ids" {
  description = "Task groups allowed HTTPS egress to the S3 managed prefix list"
  type        = map(string)
  validation {
    condition     = toset(keys(var.task_security_group_ids)) == toset(["api", "analytics", "worker", "generator"])
    error_message = "Provide exactly the four task groups, never ALB, database or cache groups."
  }
}
variable "execution_role_arns" {
  description = "Task execution roles allowed to pull images and write application logs"
  type        = set(string)
  validation {
    condition     = length(var.execution_role_arns) > 0 && alltrue([for arn in var.execution_role_arns : can(regex("^arn:aws:iam::[0-9]{12}:role/[^*?]+$", arn))])
    error_message = "Provide task execution roles."
  }
}
variable "repository_arns" {
  description = "Exact ECR repository ARNs; publishing permissions are not granted"
  type        = set(string)
  validation {
    condition     = length(var.repository_arns) == 3 && alltrue([for arn in var.repository_arns : can(regex("^arn:aws:ecr:[a-z0-9-]+:[0-9]{12}:repository/[^*?]+$", arn))])
    error_message = "Provide exactly three repository ARNs without wildcards."
  }
}
variable "log_group_arns" {
  description = "Exact CloudWatch log group ARNs without trailing wildcard"
  type        = set(string)
  validation {
    condition     = length(var.log_group_arns) > 0 && alltrue([for arn in var.log_group_arns : can(regex("^arn:aws:logs:[a-z0-9-]+:[0-9]{12}:log-group:[^*?]+$", arn))])
    error_message = "Provide exact application log group ARNs."
  }
}
variable "secret_grants" {
  description = "Named execution-role grants to exact runtime secret ARNs; never secret values"
  type = map(object({
    role_arn    = string
    secret_arns = set(string)
  }))
  validation {
    condition = length(var.secret_grants) > 0 && alltrue([
      for grant in values(var.secret_grants) : contains(var.execution_role_arns, grant.role_arn) && length(grant.secret_arns) > 0 && alltrue([
        for arn in grant.secret_arns : can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[^*?]+$", arn))
      ])
    ])
    error_message = "Each secret grant needs exact secret ARNs without wildcards."
  }
}
variable "producer_role_arn" {
  description = "Generator task role allowed to send readings"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[^*?]+$", var.producer_role_arn))
    error_message = "Provide an exact task role ARN without wildcards."
  }
}
variable "worker_role_arn" {
  description = "Worker task role allowed to receive and acknowledge readings"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:role/[^*?]+$", var.worker_role_arn))
    error_message = "Provide an exact task role ARN without wildcards."
  }
}
variable "queue_arn" {
  description = "Exact ingestion queue ARN, not the dead-letter queue"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:sqs:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+$", var.queue_arn))
    error_message = "Provide an exact standard ingestion queue ARN."
  }
}
variable "tags" {
  description = "Shared Project, Environment and ManagedBy tags"
  type        = map(string)
  default     = {}
}

variable "outbox_access" {
  description = "Optional generator-only durable outbox access; no other application bucket is allowed"
  type        = object({ bucket_arn = string, producer_role_arn = string })
  default     = null
  validation {
    condition     = var.outbox_access == null ? true : can(regex("^arn:aws:s3:::[a-z0-9][a-z0-9.-]+$", var.outbox_access.bucket_arn)) && var.outbox_access.producer_role_arn == var.producer_role_arn
    error_message = "Use an exact outbox bucket ARN and the same producer task role."
  }
}
