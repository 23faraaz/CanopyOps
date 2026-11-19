variable "name_prefix" {
  description = "Project/environment prefix for security group names"
  type        = string
}

variable "vpc_id" {
  description = "VPC containing the application and data resources"
  type        = string
}

variable "tags" {
  description = "Additional resource tags"
  type        = map(string)
  default     = {}
}

variable "alb_ingress_ipv4_cidr" {
  description = "IPv4 clients allowed to reach ALB HTTP/HTTPS; default is the public website, restrict for a restricted rehearsal"
  type        = string
  default     = "0.0.0.0/0"

  validation {
    condition     = can(cidrnetmask(var.alb_ingress_ipv4_cidr))
    error_message = "Provide a valid IPv4 CIDR; IPv6 needs separately reviewed rules."
  }
}
