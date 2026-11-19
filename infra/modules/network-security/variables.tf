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
