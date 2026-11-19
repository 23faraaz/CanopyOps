module "ecr" {
  source            = "../modules/ecr"
  repository_prefix = "${var.project_name}-${var.environment}"
  retained_images   = var.retained_images
}
