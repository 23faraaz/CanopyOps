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

variable "engine_version" {
  description = "Explicit supported Redis OSS version (7.0 or later); verify regional availability"
  type        = string
  validation {
    condition     = can(regex("^[0-9]+[.][0-9]+([.][0-9]+)?$", var.engine_version)) && try(tonumber(split(".", var.engine_version)[0]) >= 7, false)
    error_message = "Provide a Redis version 7.0 or later with required TLS/RBAC support."
  }
}
variable "node_type" {
  description = "Cost-reviewed cache node class; changing it requires a new capacity estimate"
  type        = string
  default     = "cache.t4g.micro"
}
variable "node_count" {
  description = "One disposable cache primary by default; two enables a separately priced failover rehearsal"
  type        = number
  default     = 1
  validation {
    condition     = contains([1, 2], var.node_count)
    error_message = "Choose one primary, or a primary plus one replica."
  }
}
variable "user_group_ids" {
  description = "Existing restricted Redis RBAC group IDs; provision credentials separately without reading secret values into Terraform"
  type        = set(string)
  validation {
    condition     = length(var.user_group_ids) == 1
    error_message = "This demo requires exactly one reviewed RBAC user group; unauthenticated cache access is not supported."
  }
}
variable "storage_kms_key_arn" {
  description = "Optional at-rest key ARN; null uses the service-managed encryption key"
  type        = string
  default     = null
}
