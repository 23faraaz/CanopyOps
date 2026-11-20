variable "create_application_certificate" {
  description = "Request the application certificate only after public subdomain delegation is verified"
  type        = bool
  default     = false
  validation {
    condition     = !var.create_application_certificate || (var.create_application_dns_zone && var.aws_region == "eu-west-2")
    error_message = "Certificate preparation requires the delegated zone enabled and the London provider region."
  }
}

module "application_certificate" {
  count          = var.create_application_certificate ? 1 : 0
  source         = "../modules/acm"
  domain_name    = var.application_zone_name
  hosted_zone_id = module.application_zone[0].zone_id
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

output "application_certificate_arn" {
  description = "London certificate ARN after DNS validation; pass as application.certificate_arn for ingress reuse"
  value       = try(module.application_certificate[0].certificate_arn, null)
}
