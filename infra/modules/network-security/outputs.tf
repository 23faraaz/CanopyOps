output "security_group_ids" {
  description = "Security group IDs keyed by component"
  value = {
    for name, group in aws_security_group.this : name => group.id
  }
}