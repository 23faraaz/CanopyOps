output "certificate_arn" {
  description = "Certificate ARN returned only after DNS validation completes"
  value       = aws_acm_certificate_validation.this.certificate_arn
}
