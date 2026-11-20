mock_provider "aws" {}

variables {
  domain_name = "canopyops.example.co.uk"
  tags        = { Project = "canopyops", Environment = "demo", ManagedBy = "Terraform" }
}

run "public_zone_preserves_records_and_ownership" {
  command = plan
  assert {
    condition     = aws_route53_zone.this.name == "canopyops.example.co.uk" && !aws_route53_zone.this.force_destroy
    error_message = "Use the delegated application zone and refuse forced record deletion."
  }
  assert {
    condition     = length(aws_route53_zone.this.vpc) == 0 && aws_route53_zone.this.tags.ManagedBy == "Terraform"
    error_message = "The zone must be public and consistently tagged."
  }
}

run "reject_url" {
  command = plan
  variables { domain_name = "https://example.co.uk" }
  expect_failures = [var.domain_name]
}
