variable "name_prefix" {
  description = "Reviewed project/environment prefix"
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "Use a lowercase 3-20 character prefix without wildcards."
  }
}
variable "account_id" {
  description = "Target AWS account metadata"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Use a twelve-digit account ID."
  }
}
variable "aws_region" {
  description = "The cost-reviewed London region"
  type        = string
  validation {
    condition     = var.aws_region == "eu-west-2"
    error_message = "Deployment is limited to London."
  }
}
variable "mode" {
  description = "Plan metadata reads, deploy create/update, or reviewed destroy"
  type        = string
  validation {
    condition     = contains(["plan", "deploy", "destroy"], var.mode)
    error_message = "Use plan, deploy or destroy."
  }
}
variable "role_name" {
  description = "Existing protected GitHub OIDC role for this operation"
  type        = string
  validation {
    condition     = var.role_name == "${var.name_prefix}-terraform-${var.mode}"
    error_message = "Attach only to the operation's existing protected role."
  }
}
variable "domain_name" {
  description = "Owned application hostname; this module never purchases a domain"
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9-]+([.][a-z0-9-]+)+$", var.domain_name))
    error_message = "Use an owned lowercase hostname without wildcards."
  }
}
variable "hosted_zone_id" {
  description = "The one authoritative hosted zone allowed for the two application A records"
  type        = string
  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "Use a hosted-zone ID, not a URL, wildcard or credentials."
  }
}
variable "boundary_arn" {
  description = "Bootstrap-owned immutable maximum-permission policy reference"
  type        = string
  validation {
    condition     = var.boundary_arn == "arn:aws:iam::${var.account_id}:policy/${var.name_prefix}-runtime-boundary"
    error_message = "Use this account and environment's runtime boundary."
  }
}
variable "tags" {
  description = "Shared ownership tags"
  type        = map(string)
  default     = {}
}

variable "project_name" {
  description = "Ownership project tag"
  type        = string
}
variable "environment" {
  description = "Ownership environment tag"
  type        = string
}

variable "certificate_arn" {
  description = "Exact retained bootstrap ACM certificate; application roles may reference it only"
  type        = string
  validation {
    condition     = can(regex("^arn:aws:acm:${var.aws_region}:${var.account_id}:certificate/[a-f0-9-]{36}$", var.certificate_arn))
    error_message = "Use this account's retained London certificate ARN."
  }
}
