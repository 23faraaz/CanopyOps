output "cache" {
  description = "TLS endpoint metadata; no credential values are exported"
  value       = { address = aws_elasticache_replication_group.this.primary_endpoint_address, port = aws_elasticache_replication_group.this.port, arn = aws_elasticache_replication_group.this.arn, tls_required = true }
}
