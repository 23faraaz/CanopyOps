output "database" {
  description = "Private database connection metadata, without a password or connection string"
  value       = { address = aws_db_instance.this.address, port = aws_db_instance.this.port, name = aws_db_instance.this.db_name, arn = aws_db_instance.this.arn, resource_id = aws_db_instance.this.resource_id }
}
output "master_secret_arn" {
  description = "Administrative secret reference only; do not grant application task roles access to it"
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
}
