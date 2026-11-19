variable "project_name" {
  description = "Project ownership tag and repository prefix"
  type        = string
  default     = "canopyops"
}

variable "environment" {
  description = "One portfolio demo environment"
  type        = string
  default     = "demo"
}

variable "aws_region" {
  description = "AWS region for private image storage"
  type        = string
  default     = "eu-west-2"
}

variable "retained_images" {
  description = "Images retained per repository; module validates the bounded integer range"
  type        = number
  default     = 20
}
