locals {
  identifier = "${var.name_prefix}-postgres"
  tags       = merge(var.tags, { ManagedBy = "Terraform", Service = "database" })
}
resource "aws_db_subnet_group" "this" {
  name       = local.identifier
  subnet_ids = var.subnet_ids
  tags       = merge(local.tags, { Name = local.identifier })
}
resource "aws_db_parameter_group" "this" {
  name_prefix = "${local.identifier}-"
  family      = "postgres${split(".", var.engine_version)[0]}"
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }
  tags = merge(local.tags, { Name = local.identifier })
  lifecycle { create_before_destroy = true }
}
resource "aws_cloudwatch_log_group" "postgres" {
  for_each          = toset(["postgresql", "upgrade"])
  name              = "/aws/rds/instance/${local.identifier}/${each.key}"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.log_kms_key_arn
  tags              = merge(local.tags, { Name = "${local.identifier}-${each.key}" })
}
resource "aws_db_instance" "this" {
  identifier                          = local.identifier
  engine                              = "postgres"
  engine_version                      = var.engine_version
  engine_lifecycle_support            = "open-source-rds-extended-support-disabled"
  instance_class                      = var.instance_class
  allocated_storage                   = var.allocated_storage
  max_allocated_storage               = 0
  storage_type                        = "gp3"
  storage_encrypted                   = true
  kms_key_id                          = var.storage_kms_key_arn
  db_name                             = var.snapshot_identifier == null ? var.database_name : null
  username                            = var.snapshot_identifier == null ? var.master_username : null
  snapshot_identifier                 = var.snapshot_identifier
  manage_master_user_password         = true
  iam_database_authentication_enabled = true
  port                                = 5432
  db_subnet_group_name                = aws_db_subnet_group.this.name
  vpc_security_group_ids              = var.security_group_ids
  parameter_group_name                = aws_db_parameter_group.this.name
  publicly_accessible                 = false
  multi_az                            = var.multi_az
  backup_retention_period             = 7
  backup_window                       = "02:00-03:00"
  maintenance_window                  = "sun:04:00-sun:05:00"
  auto_minor_version_upgrade          = true
  allow_major_version_upgrade         = false
  apply_immediately                   = false
  deletion_protection                 = var.deletion_protection
  skip_final_snapshot                 = false
  final_snapshot_identifier           = var.final_snapshot_identifier
  copy_tags_to_snapshot               = true
  delete_automated_backups            = false
  enabled_cloudwatch_logs_exports     = ["postgresql", "upgrade"]
  tags                                = merge(local.tags, { Name = local.identifier })
  depends_on                          = [aws_cloudwatch_log_group.postgres]
}
