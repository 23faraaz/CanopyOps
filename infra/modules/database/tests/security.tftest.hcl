mock_provider "aws" {
  override_during = plan
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-west-2:123456789012:log-group:aws-waf-logs-canopyops-test" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address            = "postgres.example.test"
      arn                = "arn:aws:rds:eu-west-2:123456789012:db:canopyops-test-postgres"
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:eu-west-2:123456789012:secret:fixture-AbCdEf", secret_status = "active", kms_key_id = "fixture" }]
    }
  }
}
variables {
  name_prefix               = "canopyops-test"
  tags                      = { Project = "canopyops", Environment = "test" }
  subnet_ids                = ["subnet-11111111", "subnet-22222222"]
  security_group_ids        = ["sg-11111111"]
  log_kms_key_arn           = "arn:aws:kms:eu-west-2:123456789012:key/11111111-1111-1111-1111-111111111111"
  engine_version            = "17.6"
  final_snapshot_identifier = "canopyops-test-final"
}
run "private_encrypted_recoverable_database" {
  command = plan
  assert {
    condition     = !aws_db_instance.this.publicly_accessible && aws_db_instance.this.storage_encrypted && aws_db_instance.this.multi_az && aws_db_instance.this.manage_master_user_password && aws_db_instance.this.iam_database_authentication_enabled
    error_message = "Keep RDS private, encrypted and Multi-AZ with AWS-managed administrative credentials."
  }
  assert {
    condition     = aws_db_instance.this.deletion_protection && !aws_db_instance.this.skip_final_snapshot && !aws_db_instance.this.delete_automated_backups && aws_db_instance.this.backup_retention_period == 7 && aws_db_instance.this.max_allocated_storage == 0
    error_message = "Retain recovery safeguards and bound storage growth."
  }
  assert {
    condition     = one(aws_db_parameter_group.this.parameter).name == "rds.force_ssl" && one(aws_db_parameter_group.this.parameter).value == "1"
    error_message = "The database must reject plaintext connections; clients must separately verify server identity."
  }
}
run "separate_snapshot_restore_input" {
  command = plan
  variables { snapshot_identifier = "canopyops-isolated-restore-fixture" }
  assert {
    condition     = aws_db_instance.this.snapshot_identifier == "canopyops-isolated-restore-fixture"
    error_message = "Restore must preserve the requested source snapshot reference."
  }
}
run "reject_undersized_storage" {
  command = plan
  variables { allocated_storage = 19 }
  expect_failures = [var.allocated_storage]
}
run "reject_missing_final_snapshot_name" {
  command = plan
  variables { final_snapshot_identifier = "" }
  expect_failures = [var.final_snapshot_identifier]
}
run "reject_single_subnet" {
  command = plan
  variables { subnet_ids = ["subnet-11111111"] }
  expect_failures = [var.subnet_ids]
}
