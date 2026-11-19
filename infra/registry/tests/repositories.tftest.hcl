mock_provider "aws" {
  override_during = plan
}

run "three_private_repository_contracts" {
  command = plan

  assert {
    condition     = toset(keys(module.ecr.repositories)) == toset(["api", "worker", "analytics"])
    error_message = "Only the three intended component repositories belong in this registry root."
  }

  assert {
    condition     = module.ecr.repositories.api.name == "canopyops-demo/api" && module.ecr.repositories.worker.name == "canopyops-demo/worker" && module.ecr.repositories.analytics.name == "canopyops-demo/analytics"
    error_message = "Repository names must match the publisher's exact IAM namespace."
  }
}

run "reject_unbounded_retention" {
  command = plan
  module {
    source = "../modules/ecr"
  }
  variables {
    repository_prefix = "canopyops-demo"
    retained_images   = 1000
  }
  expect_failures = [var.retained_images]
}

run "encryption_immutability_and_retention" {
  command = plan
  module {
    source = "../modules/ecr"
  }
  variables {
    repository_prefix = "canopyops-demo"
  }
  assert {
    condition = alltrue([
      for repo in aws_ecr_repository.this :
      repo.image_tag_mutability == "IMMUTABLE" && !repo.force_delete &&
      repo.encryption_configuration[0].encryption_type == "AES256" && repo.image_scanning_configuration[0].scan_on_push
    ])
    error_message = "Every repository must explicitly encrypt, scan and reject tag overwrites and forced Terraform deletion."
  }
  assert {
    condition = alltrue([
      for policy in aws_ecr_lifecycle_policy.this :
      jsondecode(policy.policy).rules[1].selection.countNumber == 20 &&
      jsondecode(policy.policy).rules[0].selection.countNumber == 1
    ])
    error_message = "Retain 20 images and expire untagged remnants after one day."
  }
}
