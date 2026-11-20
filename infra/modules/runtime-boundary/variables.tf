variable "name_prefix" {
  description = "Reviewed project/environment prefix"
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.name_prefix))
    error_message = "Use a 3-20 character lowercase project/environment prefix without wildcards."
  }
}
variable "aws_region" {
  description = "Application region"
  type        = string
}
variable "account_id" {
  description = "Account containing the demo resources"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.account_id))
    error_message = "Provide a twelve-digit account ID."
  }
}
variable "tags" {
  description = "Shared resource tags"
  type        = map(string)
  default     = {}
}
