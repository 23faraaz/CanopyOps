locals {
  services = toset(["api", "worker", "analytics"])
}

resource "aws_ecr_repository" "this" {
  for_each = local.services

  name                 = "${var.repository_prefix}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name    = "${var.repository_prefix}-${each.key}"
    Service = each.key
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  for_each = aws_ecr_repository.this

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged build remnants after one day"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Publishing-only retention; protect active/rollback digests before ECS deployment"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.retained_images
        }
        action = { type = "expire" }
      }
    ]
  })
}
