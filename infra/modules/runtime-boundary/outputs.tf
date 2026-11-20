output "arn" {
  description = "Boundary policy ARN; deployment identities must not be allowed to edit or remove this policy"
  value       = aws_iam_policy.runtime_boundary.arn
}
