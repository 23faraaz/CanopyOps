resource "aws_acm_certificate" "this" {
  domain_name               = var.domain_name
  subject_alternative_names = ["analytics.${var.domain_name}"]
  validation_method         = "DNS"
  key_algorithm             = "RSA_2048"
  options { certificate_transparency_logging_preference = "ENABLED" }
  tags = merge(var.tags, { Name = "${var.domain_name}-https", ManagedBy = "Terraform" })
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
