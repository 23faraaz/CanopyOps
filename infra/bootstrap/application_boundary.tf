variable "enable_application_boundary" {
  description = "Create the reviewed runtime maximum-permission boundary; does not grant deployment access"
  type        = bool
  default     = false
}
module "application_boundary" {
  count       = var.enable_application_boundary ? 1 : 0
  source      = "../modules/runtime-boundary"
  name_prefix = "${var.project_name}-${var.environment}"
  aws_region  = var.aws_region
  account_id  = data.aws_caller_identity.current.account_id
  tags        = { Project = var.project_name, Environment = var.environment, ManagedBy = "Terraform" }
}
output "application_boundary_arn" {
  description = "Task/execution permissions boundary metadata"
  value       = try(module.application_boundary[0].arn, null)
}
