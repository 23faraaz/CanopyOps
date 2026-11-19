locals {
  identifier = "${var.name_prefix}-redis"
  tags       = merge(var.tags, { ManagedBy = "Terraform", Service = "analytics-cache" })
}
resource "aws_elasticache_subnet_group" "this" {
  name       = local.identifier
  subnet_ids = var.subnet_ids
  tags       = merge(local.tags, { Name = local.identifier })
}
resource "aws_elasticache_replication_group" "this" {
  replication_group_id       = local.identifier
  description                = "Private disposable analytics cache; PostgreSQL remains authoritative"
  engine                     = "redis"
  engine_version             = var.engine_version
  node_type                  = var.node_type
  num_cache_clusters         = var.node_count
  port                       = 6379
  subnet_group_name          = aws_elasticache_subnet_group.this.name
  security_group_ids         = var.security_group_ids
  at_rest_encryption_enabled = true
  kms_key_id                 = var.storage_kms_key_arn
  transit_encryption_enabled = true
  transit_encryption_mode    = "required"
  user_group_ids             = var.user_group_ids
  automatic_failover_enabled = var.node_count > 1
  multi_az_enabled           = var.node_count > 1
  auto_minor_version_upgrade = true
  apply_immediately          = false
  snapshot_retention_limit   = 1
  snapshot_window            = "01:00-02:00"
  maintenance_window         = "sun:03:00-sun:04:00"
  tags                       = merge(local.tags, { Name = local.identifier })
}
