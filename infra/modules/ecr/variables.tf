variable "repository_prefix" {
  description = "Project/environment namespace for the three private repositories"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]+([._-][a-z0-9]+)*$", var.repository_prefix)) && length(var.repository_prefix) <= 100
    error_message = "Use a lowercase repository namespace of at most 100 characters."
  }
}

variable "retained_images" {
  description = "Maximum retained image count per repository after asynchronous lifecycle processing"
  type        = number
  default     = 20

  validation {
    condition     = var.retained_images >= 2 && var.retained_images <= 50 && floor(var.retained_images) == var.retained_images
    error_message = "Retain between 2 and 50 whole images per repository."
  }
}
