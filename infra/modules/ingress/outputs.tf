output "load_balancer" {
  description = "ALB identifiers and the validated HTTPS listener"
  value       = { arn = aws_lb.this.arn, dns_name = aws_lb.this.dns_name, zone_id = aws_lb.this.zone_id, https_listener_arn = aws_lb_listener.https.arn }
}
output "target_group_arns" {
  description = "IP target groups keyed by api and analytics; no worker or metrics target exists"
  value       = { for service, group in aws_lb_target_group.this : service => group.arn }
}
output "certificate_arn" {
  description = "Terraform-created and DNS-validated regional ACM certificate"
  value       = var.certificate_arn != null ? var.certificate_arn : aws_acm_certificate_validation.this[0].certificate_arn
}
output "urls" {
  description = "HTTPS hostnames; DNS/certificate validation and healthy targets still require live verification"
  value       = { for service, route in local.routes : service => "https://${route.host}" }
}
