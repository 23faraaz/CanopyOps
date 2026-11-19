output "state_bucket_name" {
  description = "S3 bucket used for Terraform remote state"
  value       = aws_s3_bucket.terraform_state.id
}
output "github_publisher_role_arn" {
  description = "Public role identifier for the protected publishing environment, null until explicitly enabled"
  value       = try(aws_iam_role.github_publisher[0].arn, null)
}
