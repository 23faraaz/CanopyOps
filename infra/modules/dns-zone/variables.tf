variable "domain_name" {
  description = "Owned application subdomain delegated by the parent DNS provider"
  type        = string
  validation {
    condition     = can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.){2,}[a-z]{2,}$", var.domain_name))
    error_message = "Provide a lowercase subdomain without a scheme, wildcard or trailing dot."
  }
}

variable "tags" {
  description = "Shared project, environment and Terraform ownership tags"
  type        = map(string)
}
