variable "create_application_dns_zone" {
  description = "Explicitly create the delegated public zone after reviewing its cost and plan"
  type        = bool
  default     = false
}

variable "application_zone_name" {
  description = "Owned subdomain; delegate its NS records from Cloudflare"
  type        = string
  default     = "canopyops.faraazmo.co.uk"
}

module "application_zone" {
  count       = var.create_application_dns_zone ? 1 : 0
  source      = "../modules/dns-zone"
  domain_name = var.application_zone_name
  tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

output "application_zone_id" {
  description = "Public Route 53 zone ID; null until creation is enabled and applied"
  value       = try(module.application_zone[0].zone_id, null)
}

output "application_zone_name_servers" {
  description = "Four AWS nameservers for Cloudflare NS delegation; not IPv4 addresses"
  value       = try(module.application_zone[0].name_servers, [])
}
