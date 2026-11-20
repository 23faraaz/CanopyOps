variable "name_prefix" {
  description = "Lowercase project/environment resource prefix"
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "Use a 3-20 character lowercase project/environment prefix."
  }
}
variable "aws_region" {
  description = "Deployment region"
  type        = string
}
variable "repository_arns" {
  description = "Exact ECR repository ARN per service"
  type        = map(string)
  validation {
    condition     = toset(keys(var.repository_arns)) == toset(["api", "worker", "analytics"]) && alltrue([for arn in values(var.repository_arns) : can(regex("^arn:aws:ecr:[a-z0-9-]+:[0-9]{12}:repository/[^*?]+$", arn))])
    error_message = "Provide exact api, worker and analytics repository ARNs."
  }
}
variable "queue_arn" {
  description = "Ingestion queue ARN"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:sqs:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+$", var.queue_arn))
    error_message = "Provide an exact standard queue ARN."
  }
}
variable "redis_user_ids" {
  description = "Externally provisioned Redis RBAC users, including a disabled default user; empty prepares secret metadata before credential provisioning"
  type        = set(string)
  default     = []
  validation {
    condition     = length(var.redis_user_ids) == 0 || length(var.redis_user_ids) >= 2
    error_message = "Provide the analytics user and disabled default user IDs after provisioning."
  }
}
variable "tags" {
  description = "Shared Project, Environment and ManagedBy resource tags"
  type        = map(string)
  default     = {}
}

variable "generator_image_uri" {
  description = "Optional worker image digest used by the finite generator task; null creates no task definition"
  type        = string
  default     = null
  validation {
    condition     = var.generator_image_uri == null ? true : can(regex("^[0-9]{12}[.]dkr[.]ecr[.][a-z0-9-]+[.]amazonaws[.]com/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$", var.generator_image_uri))
    error_message = "Use a scanned private ECR digest for the generator."
  }
}

variable "permissions_boundary_arn" {
  description = "Bootstrap-owned runtime boundary; deployment roles must not edit or remove it"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:iam::[0-9]{12}:policy/", var.permissions_boundary_arn)) && endswith(var.permissions_boundary_arn, "/${var.name_prefix}-runtime-boundary")
    error_message = "Use the bootstrap-owned boundary for this project/environment prefix."
  }
}
