locals {
  tags = merge(var.tags, { ManagedBy = "Terraform", Service = "ingress" })
  routes = {
    api       = { host = var.domain_name, port = 8000, priority = 10 }
    analytics = { host = "analytics.${var.domain_name}", port = 8081, priority = 20 }
  }
}
resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = ["analytics.${var.domain_name}"]
  validation_method         = "DNS"
  key_algorithm             = "RSA_2048"
  options { certificate_transparency_logging_preference = "ENABLED" }
  tags = merge(local.tags, { Name = "${var.name_prefix}-https" })
  lifecycle { create_before_destroy = true }
}
resource "aws_route53_record" "validation" {
  for_each = toset([var.domain_name, "analytics.${var.domain_name}"])
  zone_id  = var.hosted_zone_id
  name     = one([for record in aws_acm_certificate.this.domain_validation_options : record.resource_record_name if record.domain_name == each.key])
  type     = one([for record in aws_acm_certificate.this.domain_validation_options : record.resource_record_type if record.domain_name == each.key])
  records  = [one([for record in aws_acm_certificate.this.domain_validation_options : record.resource_record_value if record.domain_name == each.key])]
  ttl      = 60
}
resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for record in aws_route53_record.validation : record.fqdn]
  timeouts { create = "15m" }
}
# Intentional public HTTPS ingress for the project; review exposure/SG/WAF before deployment.
#trivy:ignore:AVD-AWS-0053:exp:2026-12-19
resource "aws_lb" "this" {
  name                       = "${var.name_prefix}-alb"
  internal                   = false
  load_balancer_type         = "application"
  ip_address_type            = "ipv4"
  subnets                    = var.subnet_ids
  security_groups            = var.security_group_ids
  enable_deletion_protection = var.deletion_protection
  drop_invalid_header_fields = true
  desync_mitigation_mode     = "strictest"
  access_logs {
    bucket  = var.access_log_bucket_name
    prefix  = var.name_prefix
    enabled = true
  }
  tags = merge(local.tags, { Name = "${var.name_prefix}-alb" })
}
resource "aws_lb_target_group" "this" {
  for_each             = local.routes
  name                 = "${var.name_prefix}-${each.key}"
  vpc_id               = var.vpc_id
  target_type          = "ip"
  protocol             = "HTTP"
  port                 = each.value.port
  deregistration_delay = 60
  health_check {
    enabled             = true
    path                = "/health/ready"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 15
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  tags = merge(local.tags, { Name = "${var.name_prefix}-${each.key}", Component = each.key })
}
resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = aws_acm_certificate_validation.this.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Unknown host"
      status_code  = "404"
    }
  }
  tags = local.tags
}
resource "aws_lb_listener_rule" "metrics_private" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 1
  condition {
    path_pattern { values = ["/metrics", "/metrics/*"] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Private endpoint"
      status_code  = "403"
    }
  }
  tags = local.tags
}
resource "aws_lb_listener_rule" "https" {
  for_each     = local.routes
  listener_arn = aws_lb_listener.https.arn
  priority     = each.value.priority
  condition {
    host_header { values = [each.value.host] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this[each.key].arn
  }
  tags = local.tags
}
resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      host        = var.domain_name
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
  tags = local.tags
}
resource "aws_lb_listener_rule" "redirect" {
  for_each     = local.routes
  listener_arn = aws_lb_listener.http.arn
  priority     = each.value.priority
  condition {
    host_header { values = [each.value.host] }
  }
  action {
    type = "redirect"
    redirect {
      host        = each.value.host
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
  tags = local.tags
}
resource "aws_route53_record" "application" {
  for_each = local.routes
  zone_id  = var.hosted_zone_id
  name     = each.value.host
  type     = "A"
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = true
  }
}
resource "aws_wafv2_web_acl" "this" {
  name  = "${var.name_prefix}-web"
  scope = "REGIONAL"
  default_action {
    allow {}
  }
  rule {
    name     = "common-threats"
    priority = 10
    override_action {
      none {}
    }
    statement {
      managed_rule_group_statement {
        name        = "AWSManagedRulesCommonRuleSet"
        vendor_name = "AWS"
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-common"
      sampled_requests_enabled   = false
    }
  }
  rule {
    name     = "per-ip-rate-limit"
    priority = 20
    action {
      block {}
    }
    statement {
      rate_based_statement {
        aggregate_key_type = "IP"
        limit              = var.rate_limit
      }
    }
    visibility_config {
      cloudwatch_metrics_enabled = true
      metric_name                = "${var.name_prefix}-rate"
      sampled_requests_enabled   = false
    }
  }
  visibility_config {
    cloudwatch_metrics_enabled = true
    metric_name                = "${var.name_prefix}-web"
    sampled_requests_enabled   = false
  }
  tags = merge(local.tags, { Name = "${var.name_prefix}-web" })
}
resource "aws_wafv2_web_acl_association" "this" {
  resource_arn = aws_lb.this.arn
  web_acl_arn  = aws_wafv2_web_acl.this.arn
}
resource "aws_cloudwatch_log_group" "waf" {
  name              = "aws-waf-logs-${var.name_prefix}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_key_arn
  tags              = merge(local.tags, { Name = "${var.name_prefix}-waf" })
}
resource "aws_wafv2_web_acl_logging_configuration" "this" {
  resource_arn            = aws_wafv2_web_acl.this.arn
  log_destination_configs = [aws_cloudwatch_log_group.waf.arn]
  dynamic "redacted_fields" {
    for_each = toset(["authorization", "cookie", "x-api-key"])
    content {
      single_header { name = redacted_fields.value }
    }
  }
}
