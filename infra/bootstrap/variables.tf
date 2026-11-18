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
