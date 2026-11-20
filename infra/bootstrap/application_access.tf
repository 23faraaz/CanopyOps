variable "enable_application_access" {
  description = "Attach reviewed cloud policies to protected OIDC roles; false grants nothing"
  type        = bool
  default     = false
  validation {
    condition     = !var.enable_application_access || (var.enable_application_boundary && var.enable_terraform_workflows && var.application_dns != null)
    error_message = "Application access requires the runtime boundary, existing workflow roles and owned DNS metadata."
  }
}
variable "application_dns" {
  description = "Owned hostname and authoritative zone metadata; no domain purchase or credentials"
  type        = object({ domain_name = string, hosted_zone_id = string })
  default     = null
}
module "application_access" {
  for_each       = var.enable_application_access ? local.terraform_ci : {}
  source         = "../modules/deployment-access"
  name_prefix    = local.registry_prefix
  aws_region     = var.aws_region
  account_id     = data.aws_caller_identity.current.account_id
  project_name   = var.project_name
  environment    = var.environment
  mode           = each.key
  role_name      = aws_iam_role.terraform_ci[each.key].name
  boundary_arn   = module.application_boundary[0].arn
  domain_name    = var.application_dns.domain_name
  hosted_zone_id = var.application_dns.hosted_zone_id
  tags           = { Project = var.project_name, Environment = var.environment, ManagedBy = "Terraform" }
}
# Local demo access gains metadata reads only. Mutation remains in protected CI.
resource "aws_iam_role_policy_attachment" "local_application_read" {
  for_each = var.enable_application_access ? {
    for name, arn in module.application_access["plan"].policy_arns : name => arn
  } : {}
  role       = aws_iam_role.terraform_demo.name
  policy_arn = each.value
}
