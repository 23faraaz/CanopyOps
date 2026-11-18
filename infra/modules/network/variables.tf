variable "vpc_cidr" {
  description = "IPv4 CIDR block for the CanopyOps VPC"
  type        = string

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR block."
  }
}

variable "public_subnet_cidrs" {
  description = "Two public ALB subnet CIDRs, ordered consistently with the availability zones"
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_cidrs) == 2 && alltrue([for cidr in var.public_subnet_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide exactly two valid IPv4 CIDR blocks for public subnets."
  }
}

variable "private_app_subnet_cidrs" {
  description = "Two private ECS and endpoint subnet CIDRs, ordered consistently with the availability zones"
  type        = list(string)

  validation {
    condition     = length(var.private_app_subnet_cidrs) == 2 && alltrue([for cidr in var.private_app_subnet_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide exactly two valid IPv4 CIDR blocks for private application subnets."
  }
}

variable "isolated_data_subnet_cidrs" {
  description = "Two isolated RDS and Redis subnet CIDRs, ordered consistently with the availability zones"
  type        = list(string)

  validation {
    condition     = length(var.isolated_data_subnet_cidrs) == 2 && alltrue([for cidr in var.isolated_data_subnet_cidrs : can(cidrnetmask(cidr))])
    error_message = "Provide exactly two valid IPv4 CIDR blocks for isolated data subnets."
  }
}

variable "availability_zones" {
  description = "Two distinct AWS availability zones, ordered consistently with each subnet CIDR list"
  type        = list(string)

  validation {
    condition = (
      length(var.availability_zones) == 2 &&
      length(distinct(var.availability_zones)) == 2
    )
    error_message = "Provide exactly two distinct availability zones."
  }
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
}