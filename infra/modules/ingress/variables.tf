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

variable "vpc_id" {
  description = "VPC shared by the public ALB and private IP targets"
  type        = string
}
variable "domain_name" {
  description = "Owned fully-qualified application hostname within the caller's Route 53 public zone; no domain is purchased here"
  type        = string
  validation {
    condition     = length(var.domain_name) <= 240 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "Use an owned lowercase DNS hostname without a protocol, wildcard or trailing dot."
  }
}
variable "hosted_zone_id" {
  description = "Existing authoritative public Route 53 hosted zone containing domain_name"
  type        = string
}
variable "access_log_bucket_name" {
  description = "Existing retained regional S3 log bucket; caller must configure ALB log delivery policy, SSE-S3, public blocking, HTTPS-only access and bounded lifecycle"
  type        = string
  validation {
    condition     = length(var.access_log_bucket_name) >= 3 && length(var.access_log_bucket_name) <= 63
    error_message = "Provide an existing S3 log bucket name."
  }
}
variable "deletion_protection" {
  description = "Disable only for a reviewed application teardown"
  type        = bool
  default     = true
}
variable "rate_limit" {
  description = "Per-IP WAF request limit per default five-minute evaluation window; validate against intended load tests"
  type        = number
  default     = 2000
  validation {
    condition     = var.rate_limit >= 100 && var.rate_limit <= 100000 && floor(var.rate_limit) == var.rate_limit
    error_message = "Choose a whole request limit between 100 and 100000."
  }
}
