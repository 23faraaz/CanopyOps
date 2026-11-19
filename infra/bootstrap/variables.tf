variable "project_name" {
  type        = string
  description = "Name of the project"
}

variable "environment" {
  type        = string
  description = "Environment"
}

variable "aws_region" {
  type        = string
  description = "Region"
}

variable "state_bucket_name" {
  type        = string
  description = "Name of the S3 bucket used for Terraform state"
}

variable "terraform_operator_arn" {
  type        = string
  description = "IAM principal allowed to assume the CanopyOps demo Terraform role"
}
variable "enable_github_publishing" {
  description = "Enable the reviewed GitHub OIDC publishing identity only after environment protections are configured"
  type        = bool
  default     = false
}

variable "existing_github_oidc_provider_arn" {
  description = "Existing account-wide GitHub OIDC provider ARN, or null to create one when enabled; inspect before applying"
  type        = string
  default     = null
}

variable "github_repository" {
  description = "Exact case-sensitive GitHub owner/repository allowed to publish"
  type        = string
  default     = "23faraaz/CanopyOps"
}

variable "github_publish_environment" {
  description = "Protected GitHub environment; must restrict deployment branches to main"
  type        = string
  default     = "ecr-publish"
}
