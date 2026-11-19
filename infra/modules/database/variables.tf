variable "name_prefix" {
  description = "Lowercase project/environment prefix used for resource names"
  type        = string
  validation {
    condition     = length(var.name_prefix) >= 3 && length(var.name_prefix) <= 20 && can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.name_prefix))
    error_message = "Use 3?20 lowercase letters/digits separated by single hyphens, starting with a letter."
  }
}
variable "tags" {
  description = "Additional tags; Project and Environment normally come from provider default_tags"
  type        = map(string)
  default     = {}
}
variable "subnet_ids" {
  description = "At least two distinct caller-selected subnets in different AZs; use private/isolated subnets except for ingress"
  type        = set(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "Provide at least two distinct subnet IDs; the caller must verify their AZs and routing."
  }
}
variable "security_group_ids" {
  description = "Caller-managed security groups with only the required traffic flows"
  type        = set(string)
  validation {
    condition     = length(var.security_group_ids) > 0
    error_message = "Provide at least one explicitly scoped security group."
  }
}
variable "log_kms_key_arn" {
  description = "Caller-managed regional KMS key ARN whose key policy permits CloudWatch Logs for these log groups"
  type        = string
  validation {
    condition     = can(regex("^arn:aws[a-z-]*:kms:[a-z0-9-]+:[0-9]{12}:key/", var.log_kms_key_arn))
    error_message = "Provide a KMS key ARN, not a key secret or alias."
  }
}
variable "log_retention_days" {
  description = "Bounded retention for service logs"
  type        = number
  default     = 7
  validation {
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "Choose 1, 3, 5, 7, 14 or 30 days."
  }
}

variable "engine_version" {
  description = "Explicit supported PostgreSQL major.minor version; verify London instance/version availability before deployment"
  type        = string
  validation {
    condition     = can(regex("^[0-9]{2}[.][0-9]+$", var.engine_version))
    error_message = "Use a PostgreSQL major.minor version, such as 17.8; this is syntax validation, not an availability guarantee."
  }
}
variable "instance_class" {
  description = "Explicitly review cost/capacity before changing the small demo instance"
  type        = string
  default     = "db.t4g.micro"
}
variable "allocated_storage" {
  description = "Provisioned gp3 GiB; storage autoscaling is disabled to bound the rehearsal"
  type        = number
  default     = 20
  validation {
    condition     = var.allocated_storage >= 20 && var.allocated_storage <= 100 && floor(var.allocated_storage) == var.allocated_storage
    error_message = "Provision 20?100 whole GiB after cost review."
  }
}
variable "database_name" {
  description = "Initial database name; SQL schemas/roles/data are managed by the migration process"
  type        = string
  default     = "canopy"
}
variable "master_username" {
  description = "Administrative bootstrap identity, never the application runtime identity"
  type        = string
  default     = "canopy_admin"
}
variable "multi_az" {
  description = "Enable the planned two-instance Multi-AZ rehearsal; a false value changes the availability/cost baseline"
  type        = bool
  default     = true
}
variable "deletion_protection" {
  description = "Disable only in a separately reviewed teardown after preserving the required backups"
  type        = bool
  default     = true
}
variable "final_snapshot_identifier" {
  description = "Unique final snapshot name for a reviewed teardown; snapshots retain data and storage charges"
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9]*(-[a-z0-9]+)*$", var.final_snapshot_identifier)) && length(var.final_snapshot_identifier) <= 63
    error_message = "Provide a unique lowercase final snapshot identifier of at most 63 characters."
  }
}
variable "snapshot_identifier" {
  description = "Optional existing PostgreSQL snapshot for a separate restore rehearsal; not a SQLite migration substitute"
  type        = string
  default     = null
}
variable "storage_kms_key_arn" {
  description = "Optional storage key ARN; null uses the AWS-managed RDS key"
  type        = string
  default     = null
}
