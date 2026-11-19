variable "name_prefix" {
  description = "Lowercase project/environment prefix used for resource names"
  type        = string
  validation {
    condition     = length(var.name_prefix) >= 3 && length(var.name_prefix) <= 20 && can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.name_prefix))
    error_message = "Use 3?20 lowercase letters/digits separated by single hyphens, starting with a letter."
  }
}
variable "tags" {
  description = "Additional tags; Project and Environment normally come from provider default_tags"
  type        = map(string)
  default     = {}
}
variable "subnet_ids" {
  description = "At least two distinct caller-selected subnets in different AZs; use private/isolated subnets except for ingress"
  type        = set(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Provide at least two distinct subnet IDs; the caller must verify their AZs and routing."
  }
}
variable "security_group_ids" {
  description = "Caller-managed security groups with only the required traffic flows"
  type        = set(string)
  validation {
    condition     = length(var.security_group_ids) > 0
    error_message = "Provide at least one explicitly scoped security group."
  }
}
variable "log_kms_key_arn" {
  description = "Caller-managed regional KMS key ARN whose key policy permits CloudWatch Logs for these log groups"
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:kms:[a-z0-9-]+:[0-9]{12}:key/", var.log_kms_key_arn))
    error_message = "Provide a KMS key ARN, not a key secret or alias."
  }
}
variable "log_retention_days" {
  description = "Bounded retention for service logs"
  type        = number
  default     = 7
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "Choose 1, 3, 5, 7, 14 or 30 days."
  }
}

variable "service_name" {
  description = "Existing application component; the finite producer is not a long-running ECS service"
  type        = string
  validation {
    condition     = contains(["api", "worker", "analytics"], var.service_name)
    error_message = "Choose api, worker or analytics."
  }
}
variable "cluster_arn" {
  description = "The same caller-managed cluster ARN for all three services; this module never creates a cluster"
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:ecs:[a-z0-9-]+:[0-9]{12}:cluster/", var.cluster_arn))
    error_message = "Provide an ECS cluster ARN."
  }
}
variable "aws_region" {
  description = "Region matching the caller provider, cluster, images and private endpoints"
  type        = string
}
variable "image_uri" {
  description = "Private ECR image pinned by scanned immutable sha256 digest; never a mutable tag"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}[.]dkr[.]ecr[.][a-z0-9-]+[.]amazonaws[.]com([.]cn)?/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$", var.image_uri))
    error_message = "Provide a private ECR repository URI followed by @sha256: and 64 lowercase hex characters."
  }
}
variable "execution_role_arn" {
  description = "Separate caller-managed role for ECR pull, log delivery and referenced runtime secrets"
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.execution_role_arn))
    error_message = "Provide an IAM execution-role ARN."
  }
}
variable "task_role_arn" {
  description = "Least-privilege application role, distinct from execution permissions"
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:iam::[0-9]{12}:role/", var.task_role_arn)) && var.task_role_arn != var.execution_role_arn
    error_message = "Provide a task-role ARN different from the execution role."
  }
}
variable "cpu" {
  description = "Cost-reviewed Fargate CPU units"
  type        = number
  default     = 512
  validation {
    condition     = contains([256, 512, 1024], var.cpu)
    error_message = "This bounded demo module supports 256, 512 or 1024 CPU units."
  }
}
variable "memory" {
  description = "Fargate memory MiB compatible with the selected CPU"
  type        = number
  default     = 1024
  validation {
    condition = contains(lookup({
      "256"  = [512, 1024, 2048], "512" = [1024, 2048, 3072, 4096]
      "1024" = [2048, 3072, 4096, 5120, 6144, 7168, 8192]
    }, tostring(var.cpu), []), var.memory)
    error_message = "Choose a supported Fargate CPU/memory pair."
  }
}
variable "desired_count" {
  description = "One task normally; zero pauses a service, two is a separately priced availability exercise"
  type        = number
  default     = 1
  validation {
    condition     = contains([0, 1, 2], var.desired_count)
    error_message = "Desired count must be 0, 1 or 2."
  }
}
variable "environment_variables" {
  description = "Non-secret application settings only; credentials belong in Secrets Manager/SSM references"
  type        = map(string)
  default     = {}
  validation {
    condition = alltrue([for name in keys(var.environment_variables) :
      length(regexall("(?i)(password|token|secret|credential|api.?key)", name)) == 0
    ]) && length(setintersection(toset(keys(var.environment_variables)), toset(keys(var.secrets)))) == 0 && length(setintersection(toset(keys(var.environment_variables)), toset(["HOST", "PORT", "BACKEND", "METRICS_HOST", "METRICS_PORT"]))) == 0
    error_message = "Use secret ARN references for credential settings, avoid duplicate secret/environment names, and leave listener settings to the module."
  }
}
variable "secrets" {
  description = "Container setting names mapped to runtime Secrets Manager/SSM ARNs (optional JSON selectors), never plaintext values"
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for reference in values(var.secrets) : can(regex("^arn:aws[a-z-]*:(secretsmanager|ssm):[a-z0-9-]+:[0-9]{12}:", reference))]) && length(setintersection(toset(keys(var.secrets)), toset(["HOST", "PORT", "BACKEND", "METRICS_HOST", "METRICS_PORT"]))) == 0
    error_message = "Use AWS secret/parameter ARN references and do not override module-managed listener settings."
  }
}
variable "load_balancer" {
  description = "Optional private IP target group for api/analytics; the worker must not be publicly routed"
  type        = object({ target_group_arn = string })
  default     = null
  validation {
    condition     = var.service_name == "worker" ? var.load_balancer == null : true
    error_message = "Do not attach the worker/metrics port to an ALB."
  }
}
