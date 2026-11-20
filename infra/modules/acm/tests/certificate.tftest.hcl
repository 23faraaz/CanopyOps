mock_provider "aws" {
  override_during = plan
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
      domain_validation_options = [
        { domain_name = "canopyops.example.test", resource_record_name = "_api.canopyops.example.test", resource_record_type = "CNAME", resource_record_value = "_api.acm-validations.aws." },
        { domain_name = "analytics.canopyops.example.test", resource_record_name = "_analytics.analytics.canopyops.example.test", resource_record_type = "CNAME", resource_record_value = "_analytics.acm-validations.aws." }
      ]
    }
  }
}
variables {
  domain_name    = "canopyops.example.test"
  hosted_zone_id = "Z1111111111111"
  tags           = { Project = "canopyops", Environment = "test" }
}
run "dns_validation_for_exact_hosts" {
  command = plan
  assert {
    condition     = aws_acm_certificate.this.validation_method == "DNS" && aws_acm_certificate.this.key_algorithm == "RSA_2048" && toset(aws_acm_certificate.this.subject_alternative_names) == toset(["analytics.canopyops.example.test"])
    error_message = "Use DNS validation and only the application and analytics hosts."
  }
  assert {
    condition     = length(aws_route53_record.validation) == 2 && alltrue([for record in aws_route53_record.validation : record.zone_id == var.hosted_zone_id && record.type == "CNAME" && record.ttl == 60])
    error_message = "Create both validation CNAMEs in the intended public zone."
  }
}
run "reject_url" {
  command = plan
  variables { domain_name = "https://canopyops.example.test" }
  expect_failures = [var.domain_name]
}
run "reject_invalid_zone" {
  command = plan
  variables { hosted_zone_id = "example.test" }
  expect_failures = [var.hosted_zone_id]
}
