output "policy_arns" {
  description = "Operation-specific managed-policy references for verification"
  value       = { for name, policy in aws_iam_policy.this : name => policy.arn }
}
