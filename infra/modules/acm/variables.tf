variable "domain_name" {
  description = "Owned application hostname in the delegated public zone"
  type        = string
  validation {
    condition     = length(var.domain_name) <= 240 && can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?([.][a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "Provide a lowercase hostname, without a scheme, wildcard or trailing dot."
  }
}
variable "hosted_zone_id" {
  description = "Public authoritative Route 53 zone ID; not a domain name or credential"
  type        = string
  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "Provide a Route 53 hosted zone ID starting with Z."
  }
}
variable "tags" {
  description = "Shared project and environment ownership tags"
  type        = map(string)
}
