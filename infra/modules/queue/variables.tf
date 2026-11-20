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

variable "retention_seconds" {
  description = "Bounded source queue retention, independent of database history"
  type        = number
  default     = 86400
  validation {
    condition     = var.retention_seconds >= 60 && var.retention_seconds <= 345600 && floor(var.retention_seconds) == var.retention_seconds
    error_message = "Use whole seconds between one minute and four days."
  }
}
variable "dead_letter_retention_seconds" {
  description = "DLQ retention must exceed source retention because standard messages retain their original enqueue timestamp"
  type        = number
  default     = 345600
  validation {
    condition     = var.dead_letter_retention_seconds > var.retention_seconds && var.dead_letter_retention_seconds <= 1209600 && floor(var.dead_letter_retention_seconds) == var.dead_letter_retention_seconds
    error_message = "DLQ retention must be longer than source retention and at most 14 days."
  }
}
variable "visibility_timeout_seconds" {
  description = "Must match worker QUEUE_VISIBILITY_SECONDS and exceed expected durable processing time"
  type        = number
  default     = 120
  validation {
    condition     = var.visibility_timeout_seconds >= 30 && var.visibility_timeout_seconds <= 43200 && floor(var.visibility_timeout_seconds) == var.visibility_timeout_seconds
    error_message = "Choose 30?43200 whole seconds."
  }
}
variable "max_receive_count" {
  description = "Deliveries before moving a repeatedly failing message to the DLQ"
  type        = number
  default     = 5
  validation {
    condition     = var.max_receive_count >= 2 && var.max_receive_count <= 10 && floor(var.max_receive_count) == var.max_receive_count
    error_message = "Choose 2?10 whole deliveries."
  }
}

variable "writes_paused" {
  description = "Explicitly fence new sends to the ingestion queue while draining or preparing migration/teardown"
  type        = bool
  default     = false
}
