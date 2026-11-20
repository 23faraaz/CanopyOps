variable "application" {
  description = "Optional reviewed cloud configuration. Null preserves the existing network-only scope; contains metadata only, never credential values."
  type = object({
    permissions_boundary_arn  = string
    domain_name               = string
    hosted_zone_id            = string
    certificate_arn           = optional(string)
    postgres_version          = string
    redis_version             = string
    final_snapshot_identifier = string
    redis_user_ids            = optional(set(string), [])
    repository_arns           = map(string)
    image_uris                = map(string)
    activate_services         = optional(bool, false)
    pause_ingestion           = optional(bool, false)
    teardown_prepared         = optional(bool, false)
  })
  default = null
  validation {
    condition     = var.application == null ? true : toset(keys(var.application.image_uris)) == toset(["api", "worker", "analytics"]) && alltrue([for image in values(var.application.image_uris) : can(regex("@sha256:[a-f0-9]{64}$", image))])
    error_message = "Provide exactly three scanned images pinned by immutable digest."
  }
  validation {
    condition     = var.application == null ? true : var.aws_region == "eu-west-2"
    error_message = "This cost-reviewed cloud runtime and RDS trust bundle support London only."
  }

  validation {
    condition     = var.application == null ? true : !var.application.activate_services || length(var.application.redis_user_ids) >= 2
    error_message = "Provision the Redis users and matching runtime secrets before explicitly activating services."
  }

  validation {
    condition     = var.application == null ? true : startswith(var.application.final_snapshot_identifier, "${var.project_name}-${var.environment}-")
    error_message = "The final snapshot identifier must use the project/environment prefix."
  }

  validation {
    condition     = var.application == null ? true : !var.application.teardown_prepared || (!var.application.activate_services && var.application.pause_ingestion)
    error_message = "Teardown preparation requires stopped services and fenced ingestion after draining the queue."
  }

}
