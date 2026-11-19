mock_provider "aws" {
  override_during = plan
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-test" }
  }
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:eu-west-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
      domain_validation_options = [
        { domain_name = "app.example.test", resource_record_name = "_api.app.example.test", resource_record_type = "CNAME", resource_record_value = "_fixture.acm-validations.aws." },
        { domain_name = "analytics.app.example.test", resource_record_name = "_analytics.app.example.test", resource_record_type = "CNAME", resource_record_value = "_fixture.acm-validations.aws." }
      ]
    }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:loadbalancer/app/fixture/1111111111111111", dns_name = "fixture.eu-west-2.elb.amazonaws.com", zone_id = "Z32O12XQLNTSW2" }
  }
  mock_resource "aws_lb_listener" {
    defaults = { arn = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:listener/app/fixture/1111111111111111/2222222222222222" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:eu-west-2:123456789012:targetgroup/fixture/1111111111111111" }
  }
  mock_resource "aws_wafv2_web_acl" {
    defaults = { arn = "arn:aws:wafv2:eu-west-2:123456789012:regional/webacl/fixture/11111111-1111-1111-1111-111111111111" }
  }
}
variables {
  name_prefix            = "canopyops-test"
  tags                   = { Project = "canopyops", Environment = "test" }
  subnet_ids             = ["subnet-11111111", "subnet-22222222"]
  security_group_ids     = ["sg-11111111"]
  log_kms_key_arn        = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
  vpc_id                 = "vpc-11111111"
  domain_name            = "app.example.test"
  hosted_zone_id         = "Z1111111111111"
  access_log_bucket_name = "fixture-retained-alb-logs"
}
run "dns_validated_https_and_private_metrics" {
  command = plan
  assert {
    condition     = aws_acm_certificate.this.validation_method == "DNS" && length(aws_route53_record.validation) == 2 && aws_lb_listener.https.protocol == "HTTPS" && one(aws_lb_listener.http.default_action).type == "redirect" && one(one(aws_lb_listener.http.default_action).redirect).protocol == "HTTPS" && aws_lb_listener.https.ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    error_message = "Create and DNS-validate ACM before serving HTTPS."
  }
  assert {
    condition     = aws_lb_target_group.this["api"].port == 8000 && aws_lb_target_group.this["analytics"].port == 8081 && length(aws_lb_target_group.this) == 2 && one(aws_lb_target_group.this["api"].health_check).path == "/health/ready" && one(aws_lb_listener_rule.metrics_private.action).fixed_response[0].status_code == "403"
    error_message = "Route the actual HTTP ports with readiness checks and block public metrics paths."
  }
  assert {
    condition     = aws_lb.this.drop_invalid_header_fields && aws_lb.this.enable_deletion_protection && one(aws_lb.this.access_logs).enabled && aws_wafv2_web_acl_association.this.resource_arn == aws_lb.this.arn && !one(aws_wafv2_web_acl.this.visibility_config).sampled_requests_enabled
    error_message = "Preserve ALB hardening/logging and WAF association without unredacted request sampling."
  }
}
run "reject_protocol_in_domain" {
  command = plan
  variables { domain_name = "https://app.example.test" }
  expect_failures = [var.domain_name]
}
run "reject_unbounded_waf_rate" {
  command = plan
  variables { rate_limit = 100001 }
  expect_failures = [var.rate_limit]
}
